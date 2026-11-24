// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CalendarBand } from "@/components/app/calendar-upcoming";
import { ApiError } from "@/lib/api/client";
import { formatDuration, formatEventDay, formatEventTime } from "@/lib/format";
import type { CalendarEvent, CalendarState } from "@/lib/api/types";
import {
  calendarBody,
  settingsBody,
  settingsError,
  skipBody,
  unansweredBody,
  workspaceCalendarBody,
  workspaceCalendarError,
} from "./helpers";

const mocks = vi.hoisted(() => ({ override: vi.fn(), get: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { calendar: { override: mocks.override, get: mocks.get } } }));

beforeEach(() => {
  mocks.override.mockReset();
  mocks.get.mockReset();
});

// The calendar on the Meetings screen, fed the bodies captured from the running app
// (docs/fe-captures/calendar-state/).

afterEach(cleanup);

const notConnected = calendarBody<CalendarState>("05-state-not-connected.json");
const justConnected = calendarBody<CalendarState>("08-state-connected-before-first-sync.json");
const withEvents = calendarBody<CalendarState>("09-state-connected-with-upcoming.json");
const organizerRule = calendarBody<CalendarState>("10-state-rule-organizer.json");
const noRule = calendarBody<CalendarState>("11-state-rule-none.json");
const needsReconnect = calendarBody<CalendarState>("15-state-needs-reconnect-revoked.json");

// The capture was made on 2026-11-04 at about 14:57 UTC; the list is read at 15:20 UTC that day.
const NOW = new Date("2026-11-04T15:20:00Z");
const show = (state: CalendarState | null, tz = "UTC") =>
  render(<CalendarBand state={state} timeZone={tz} now={NOW} />);
const rows = () => within(screen.getByRole("list", { name: "Upcoming meetings" })).getAllByRole("listitem");

describe("the operator has paused auto-join", () => {
  const paused = calendarBody<CalendarState>("22-state-auto-join-paused.json");

  it("puts the server's line above the list, the rows unchanged", () => {
    show({ ...withEvents, auto_join_paused: true, auto_join_paused_text: paused.auto_join_paused_text });
    expect(
      screen.getByText("Auto-join is paused for now. The note taker is not joining meetings from your calendar."),
    ).toBeTruthy();
    expect(rows().length).toBeGreaterThan(0);
  });

  it("draws no line when it is not paused", () => {
    show(withEvents);
    expect(screen.queryByText(/Auto-join is paused/)).toBeNull();
  });
});

// LEAD-DESIGNED (2026-11-21): a row whose switch is not "Will join" says why in a visible muted line,
// not only in the tooltip. The bodies are the ones captured from the running app with the box off and
// on (docs/fe-captures/join-unanswered/).
describe("the reason is visible text", () => {
  const WAITING = "Waiting for you to accept the invite. Settings can join these too.";
  const boxOff = unansweredBody<CalendarState>("05-state-default.json");
  const boxOn = unansweredBody<CalendarState>("07-settings-box-on.json");
  const rowOf = (state: CalendarState, title: string) => {
    show(state);
    return rows().find((row) => within(row).queryByText(title) !== null) as HTMLElement;
  };

  it("shows the server's not-accepted reason under the platform line of an Off row, once, with no tooltip repeating it", () => {
    const row = rowOf(boxOff, "Partnership idea");
    const reason = within(row).getByText(WAITING);
    expect(reason.closest("p")?.className).toBe("meta-line");
    expect(reason.closest("p")?.previousElementSibling?.textContent).toContain("Google Meet");
    expect(within(row).getByText("Off").closest("[title]")).toBeNull();
    expect(within(row).getByRole("switch").getAttribute("aria-describedby")).toBe(reason.id);
  });

  it("shows the reason of a row that cannot be joined too", () => {
    const row = rowOf(boxOff, "Budget review");
    expect(within(row).getByText("You declined this invite").closest("p")?.className).toBe("meta-line");
  });

  it("draws no reason line for a row that will join", () => {
    const row = rowOf(boxOn, "Partnership idea");
    expect(within(row).getByText("Will join")).toBeTruthy();
    expect(row.querySelectorAll("p.meta-line")).toHaveLength(1); // the platform line only
    expect(within(row).queryByText(WAITING)).toBeNull();
  });

  it("takes the line away when the switch is pressed to on, and brings the new one back from the server's answer", async () => {
    const off = boxOff.upcoming.find((e) => e.title === "Partnership idea") as CalendarEvent;
    mocks.override.mockResolvedValue({ ...off, will_join: true, will_join_text: "Will join", reason_text: null });
    const row = rowOf(boxOff, "Partnership idea");
    await userEvent.click(within(row).getByRole("switch"));
    await waitFor(() => expect(within(row).queryByText(WAITING)).toBeNull());
  });
});

// LEAD-DESIGNED (2026-11-24): a meeting skipped by "Skip one-on-one meetings" is an Off row like the
// others: the server's reason is the visible line, and the person's own switch still turns it on.
// The bodies are the ones captured from the running app (docs/fe-captures/skip-one-on-ones/).
describe("a skipped one-on-one", () => {
  const SKIPPED = "Skipped because only two people are invited. Settings can join these too.";
  const skipOn = skipBody<CalendarState>("07-settings-skip-on.json");
  const skipOff = skipBody<CalendarState>("05-state-default.json");
  const AT = new Date("2026-11-24T12:05:00Z");
  const rowOf = (state: CalendarState, title: string) => {
    render(<CalendarBand state={state} timeZone="UTC" now={AT} />);
    return rows().find((row) => within(row).queryByText(title) !== null) as HTMLElement;
  };

  it("shows Off with the server's reason as the visible line, and the header names the exception", () => {
    const row = rowOf(skipOn, "1:1 with Sam");
    expect(within(row).getByText("Off")).toBeTruthy();
    const reason = within(row).getByText(SKIPPED);
    expect(reason.closest("p")?.className).toBe("meta-line");
    expect(within(row).getByRole("switch").getAttribute("aria-describedby")).toBe(reason.id);
    expect(screen.getByText(skipOn.rule_text)).toBeTruthy();
  });

  it("leaves meetings with no, one and three people invited as Will join", () => {
    render(<CalendarBand state={skipOn} timeZone="UTC" now={AT} />);
    for (const title of ["Design review", "Just me on the list", "Focus block (no invite list)"]) {
      const row = rows().find((r) => within(r).queryByText(title) !== null) as HTMLElement;
      expect(within(row).getByText("Will join")).toBeTruthy();
      expect(within(row).queryByText(SKIPPED)).toBeNull();
    }
  });

  it("is Will join again once the box is cleared", () => {
    const row = rowOf(skipOff, "1:1 with Sam");
    expect(within(row).getByText("Will join")).toBeTruthy();
    expect(within(row).queryByText(SKIPPED)).toBeNull();
  });

  it("is switched on by hand, and the reason line goes", async () => {
    const off = skipOn.upcoming.find((e) => e.title === "1:1 with Sam") as CalendarEvent;
    mocks.override.mockResolvedValue({ ...off, will_join: true, will_join_text: "Will join", reason_text: null });
    const row = rowOf(skipOn, "1:1 with Sam");
    await userEvent.click(within(row).getByRole("switch"));
    await waitFor(() => expect(within(row).queryByText(SKIPPED)).toBeNull());
    expect(mocks.override).toHaveBeenCalledWith(off.id, true);
  });
});

describe("no calendar connected", () => {
  it("draws the designer's band that leads to Settings", () => {
    show(notConnected);
    expect(screen.getByText("Join meetings automatically")).toBeTruthy();
    expect(
      screen.getByText("Connect your calendar and the note taker will join upcoming meetings that have a video link."),
    ).toBeTruthy();
    const link = screen.getByRole("link", { name: "Connect calendar" });
    expect(link.getAttribute("href")).toBe("/settings");
    expect(screen.queryByText("Upcoming")).toBeNull();
  });

  it("draws the lead-designed disconnected banner, with Reconnect to Settings, when Google needs the person to connect again", () => {
    show(needsReconnect);
    expect(screen.getByText("Your calendar is disconnected")).toBeTruthy();
    expect(
      screen.getByText("The note taker will not join meetings from your calendar until you connect again."),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Reconnect" }).getAttribute("href")).toBe("/settings");
    expect(screen.queryByRole("button", { name: /close|dismiss/i })).toBeNull();
    expect(screen.queryByText("Join meetings automatically")).toBeNull();
    expect(screen.queryByText("Upcoming")).toBeNull();
  });

  it("draws nothing at all while the calendar is off or could not be read", () => {
    const { container } = show(null);
    expect(container.innerHTML).toBe("");
  });
});

describe("connected", () => {
  it("shows Upcoming with the account and the rule label that leads to Settings", () => {
    show(withEvents);
    expect(screen.getByRole("heading", { level: 2, name: "Upcoming" })).toBeTruthy();
    expect(screen.getByText("grace@example.test")).toBeTruthy();
    const rule = screen.getByRole("link", { name: "Joining all meetings" });
    expect(rule.getAttribute("href")).toBe("/settings");
    expect(screen.queryByRole("link", { name: "Connect calendar" })).toBeNull();
  });

  it("labels the rule as the person set it", () => {
    show(organizerRule);
    expect(screen.getByRole("link", { name: "Joining meetings you organise" })).toBeTruthy();
    cleanup();
    show(noRule);
    expect(screen.getByRole("link", { name: "Auto-join is off" })).toBeTruthy();
  });

  it("draws a row per meeting: day, time, title, platform, duration, people and the state", () => {
    show(withEvents);
    const list = rows();
    expect(list).toHaveLength(7);
    const first = list[0];
    expect(within(first).getByText("Today")).toBeTruthy();
    expect(within(first).getByText("16:57")).toBeTruthy();
    expect(within(first).getByText("Weekly planning")).toBeTruthy();
    expect(within(first).getByText("Google Meet")).toBeTruthy();
    expect(within(first).getByText("30 min")).toBeTruthy();
    expect(within(first).getByText("2 people")).toBeTruthy();
    expect(within(first).getByText("Will join")).toBeTruthy();
    expect(within(list[3]).getByText("Tomorrow")).toBeTruthy();
    expect(within(list[5]).getByText("Fri 6 Nov")).toBeTruthy();
  });

  it("says No meeting link for an event without one, as the design does, and Cannot join", () => {
    show(withEvents);
    const lunch = rows()[2];
    expect(within(lunch).getByText("No meeting link")).toBeTruthy();
    expect(within(lunch).getByText("Cannot join")).toBeTruthy();
    expect(within(lunch).getByText("Add a meeting link to this event to record it")).toBeTruthy();
    expect(within(lunch).getByText("Cannot join").closest("[title]")).toBeNull();
  });

  it("shows Off with its reason as a line, and the designer's three words only", () => {
    show(withEvents);
    const words = rows().map((row) => within(row).getByText(/^(Will join|Off|Cannot join)$/).textContent);
    expect(words).toEqual(["Will join", "Will join", "Cannot join", "Cannot join", "Cannot join", "Will join", "Off"]);
    expect(within(rows()[6]).getByText("Waiting for you to accept the invite")).toBeTruthy();
    expect(within(rows()[6]).getByText("Off").closest("[title]")).toBeNull();
    expect(within(rows()[0]).getByText("Will join").closest("[title]")).toBeNull();
  });

  it("round-trips an emoji and non-Latin title", () => {
    show(withEvents);
    expect(within(rows()[5]).getByText("Sprint 🚀 planning · 週次")).toBeTruthy();
  });

  it("shows the guest count as a number only, with no address or link anywhere", () => {
    show(withEvents);
    expect(document.body.textContent).not.toMatch(/@example\.test.*@|PLACEHOLDERPASSCODE|zoom\.us|pwd=|colleague@|stranger@/);
    const one: CalendarState = { ...withEvents, upcoming: [{ ...withEvents.upcoming[0], attendee_count: 1 }] };
    cleanup();
    show(one);
    expect(screen.getByText("1 person")).toBeTruthy();
    const none: CalendarState = { ...withEvents, upcoming: [{ ...withEvents.upcoming[0], attendee_count: 0 }] };
    cleanup();
    show(none);
    expect(screen.queryByText(/person|people/)).toBeNull();
  });

  it("says what is missing when there is nothing to show", () => {
    show(justConnected);
    expect(screen.getByText("Your meetings appear here after the first update.")).toBeTruthy();
    cleanup();
    show({ ...withEvents, upcoming: [] });
    expect(screen.getByText("Nothing in the next 7 days.")).toBeTruthy();
  });

  it("shows the viewer's own day and time, not UTC", () => {
    show(withEvents, "Africa/Lagos"); // UTC+1: 16:57Z is 17:57
    expect(within(rows()[0]).getByText("17:57")).toBeTruthy();
    expect(within(rows()[0]).getByText("Today")).toBeTruthy();
  });
});

describe("day, time and duration words", () => {
  const now = new Date("2026-11-04T22:30:00Z");
  it("says Today, Tomorrow and the weekday and date, in the viewer's zone", () => {
    expect(formatEventDay("2026-11-04T23:30:00Z", now, "UTC")).toBe("Today");
    expect(formatEventDay("2026-11-05T09:00:00Z", now, "UTC")).toBe("Tomorrow");
    expect(formatEventDay("2026-11-06T09:00:00Z", now, "UTC")).toBe("Fri 6 Nov");
    // 23:30Z is already tomorrow in Lagos (UTC+1), so from there "now" is 23:30 on the 4th too
    expect(formatEventDay("2026-11-04T23:30:00Z", now, "Africa/Lagos")).toBe("Tomorrow");
    expect(formatEventDay("2026-11-30T09:00:00Z", new Date("2026-11-29T09:00:00Z"), "UTC")).toBe("Tomorrow");
    expect(formatEventDay("2026-12-01T09:00:00Z", new Date("2026-11-30T09:00:00Z"), "UTC")).toBe("Tomorrow");
  });
  it("writes the time as 24-hour hours and minutes", () => {
    expect(formatEventTime("2026-11-04T09:05:00Z", "UTC")).toBe("09:05");
    expect(formatEventTime("2026-11-04T00:00:00Z", "UTC")).toBe("00:00");
  });
  it("writes durations like the design: 30 min, 1 hr", () => {
    expect(formatDuration("2026-11-04T09:00:00Z", "2026-11-04T09:30:00Z")).toBe("30 min");
    expect(formatDuration("2026-11-04T09:00:00Z", "2026-11-04T10:00:00Z")).toBe("1 hr");
    expect(formatDuration("2026-11-04T09:00:00Z", "2026-11-04T10:30:00Z")).toBe("1 hr 30 min");
    expect(formatDuration("2026-11-04T09:00:00Z", "2026-11-04T09:00:00Z")).toBe("");
  });
});


// The switch on each row (the designer's `u.toggle`, `u.state`, `u.hint`): fed the bodies the
// override route really answered (docs/fe-captures/calendar-settings/).
describe("the switch on each row", () => {
  const switches = () => screen.getAllByRole("switch") as HTMLElement[];
  const switchIn = (row: HTMLElement) => within(row).getByRole("switch");
  const nowOff = settingsBody<CalendarEvent>("22-override-off.json");
  const invited = settingsBody<CalendarEvent>("26-override-stranger-invite-on.json");

  it("draws one 36px switch per row, on for Will join, off for Off, and disabled for Cannot join", () => {
    show(withEvents);
    expect(switches()).toHaveLength(7);
    const words = rows().map((row) => within(row).getByText(/^(Will join|Off|Cannot join)$/).textContent);
    expect(words).toEqual(["Will join", "Will join", "Cannot join", "Cannot join", "Cannot join", "Will join", "Off"]);
    expect(switches().map((s) => s.getAttribute("aria-checked"))).toEqual([
      "true", "true", "false", "false", "false", "true", "false",
    ]);
    expect(switches().map((s) => s.getAttribute("aria-disabled"))).toEqual([
      null, null, "true", "true", "true", null, null,
    ]);
    const track = within(rows()[0]).getByRole("switch").querySelector(".cal-switch");
    expect(track).toBeTruthy();
  });

  it("names each switch by its meeting and its state words", () => {
    show(withEvents);
    expect(switchIn(rows()[0]).getAttribute("aria-labelledby")).toBeTruthy();
    expect(screen.getByRole("switch", { name: "Weekly planning Will join" })).toBeTruthy();
    expect(screen.getByRole("switch", { name: "Partnership idea Off" })).toBeTruthy();
  });

  it("switches a Will join meeting off through the override route and shows the answer", async () => {
    mocks.override.mockResolvedValue(nowOff);
    show(withEvents);
    await userEvent.click(switchIn(rows()[0]));
    expect(mocks.override).toHaveBeenCalledWith(nowOff.id, false);
    await waitFor(() => expect(within(rows()[0]).getByText("Off")).toBeTruthy());
    expect(within(rows()[0]).getByText("You switched this meeting off")).toBeTruthy();
    expect(switchIn(rows()[0]).getAttribute("aria-checked")).toBe("false");
    expect(switchIn(rows()[1]).getAttribute("aria-checked")).toBe("true"); // the others stay as they were
  });

  it("switches an Off meeting on, the one-click way into an invite that was not accepted", async () => {
    mocks.override.mockResolvedValue(invited);
    show(withEvents);
    const row = rows()[6];
    expect(within(row).getByText("Waiting for you to accept the invite")).toBeTruthy();
    await userEvent.click(switchIn(row));
    expect(mocks.override).toHaveBeenCalledWith(invited.id, true);
    await waitFor(() => expect(within(rows()[6]).getByText("Will join")).toBeTruthy());
    expect(switchIn(rows()[6]).getAttribute("aria-checked")).toBe("true");
    expect(switchIn(rows()[6]).getAttribute("title")).toBeNull();
  });

  it("shows the new position at once, then holds the switch until the server has answered", async () => {
    let answer: (event: CalendarEvent) => void = () => {};
    mocks.override.mockReturnValue(new Promise<CalendarEvent>((resolve) => (answer = resolve)));
    show(withEvents);
    await userEvent.click(switchIn(rows()[0]));
    expect(switchIn(rows()[0]).getAttribute("aria-checked")).toBe("false");
    await userEvent.click(switchIn(rows()[0])); // a second press while it is saving does nothing
    expect(mocks.override).toHaveBeenCalledTimes(1);
    answer(nowOff);
    await waitFor(() => expect(within(rows()[0]).getByText("Off")).toBeTruthy());
  });

  it("does nothing for a meeting that cannot be joined, and shows the designer's hint on hover", async () => {
    show(withEvents);
    const row = rows()[2];
    expect(within(row).getByText("No meeting link")).toBeTruthy();
    expect(within(row).getByText("Add a meeting link to this event to record it")).toBeTruthy();
    await userEvent.click(switchIn(row));
    expect(mocks.override).not.toHaveBeenCalled();
    expect(switchIn(row).getAttribute("aria-checked")).toBe("false");
  });

  it("dims the Cannot join rows' state and switch, as the design does", () => {
    show(withEvents);
    expect(within(rows()[2]).getByRole("switch").className).toContain("cal-up-state-off");
    expect(within(rows()[0]).getByRole("switch").className).not.toContain("cal-up-state-off");
  });

  it("puts the switch back and says in plain words that the server could not be reached", async () => {
    mocks.override.mockRejectedValue(
      new ApiError(0, "network_error", "Could not reach the server. Check your connection and try again."),
    );
    show(withEvents);
    await userEvent.click(switchIn(rows()[0]));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Could not reach the server"));
    expect(switchIn(rows()[0]).getAttribute("aria-checked")).toBe("true");
    expect(within(rows()[0]).getByText("Will join")).toBeTruthy();
  });

  it("explains a 409 in the server's words, puts the switch back and reads the list again", async () => {
    const refused = settingsError("28-override-409-declined.json");
    mocks.override.mockRejectedValue(new ApiError(refused.status, refused.code, refused.message));
    const refreshed: CalendarState = {
      ...withEvents,
      upcoming: withEvents.upcoming.map((e, i) =>
        i === 0 ? { ...e, will_join: false, will_join_text: "Cannot join", reason_text: "You declined this invite" } : e,
      ),
    };
    mocks.get.mockResolvedValue(refreshed);
    show(withEvents);
    await userEvent.click(switchIn(rows()[0]));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("You declined this invite"));
    expect(mocks.get).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(within(rows()[0]).getByText("Cannot join")).toBeTruthy());
    expect(switchIn(rows()[0]).getAttribute("aria-disabled")).toBe("true");
  });

  it("keeps the 409 message even when the list cannot be read again", async () => {
    mocks.override.mockRejectedValue(new ApiError(409, "not_joinable", "Already over"));
    mocks.get.mockRejectedValue(new ApiError(0, "network_error", "Could not reach the server."));
    show(withEvents);
    await userEvent.click(switchIn(rows()[0]));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Already over"));
    expect(switchIn(rows()[0]).getAttribute("aria-checked")).toBe("true");
  });

  it("never shows developer text for a 404 (the meeting is gone)", async () => {
    const gone = settingsError("35-override-foreign-event.json");
    mocks.override.mockRejectedValue(new ApiError(gone.status, gone.code, gone.message));
    show(withEvents);
    await userEvent.click(switchIn(rows()[0]));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Something went wrong. Please try again."));
  });

  it("clears the message when the next switch works", async () => {
    mocks.override.mockRejectedValueOnce(new ApiError(0, "network_error", "Could not reach the server."));
    mocks.override.mockResolvedValueOnce(nowOff);
    show(withEvents);
    await userEvent.click(switchIn(rows()[0]));
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    await userEvent.click(switchIn(rows()[0]));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("works with the keyboard: Space and Enter press a switch", async () => {
    mocks.override.mockResolvedValue(nowOff);
    show(withEvents);
    switchIn(rows()[0]).focus();
    await userEvent.keyboard(" ");
    expect(mocks.override).toHaveBeenCalledWith(nowOff.id, false);
  });

  it("draws no switch in the empty list or on the connect band", () => {
    show(justConnected);
    expect(screen.queryAllByRole("switch")).toHaveLength(0);
    cleanup();
    show(notConnected);
    expect(screen.queryAllByRole("switch")).toHaveLength(0);
  });
});

// LEAD-DESIGNED (2026-11-25, workspaces): the same meeting on the calendar of two of the person's
// workspaces is recorded by ONE of them. The other's row says where ("Recorded in Luran. Switch here
// instead.") and has a Switch here button; the switch is the same override write, and its answer
// carries a note that the recording moved. The bodies are the ones captured from the running app
// (docs/fe-captures/workspaces/calendar/).
describe("a meeting another workspace records", () => {
  const elsewhere = workspaceCalendarBody<CalendarState>("16-upcoming-luran-recorded-elsewhere.json");
  const moved = workspaceCalendarBody<CalendarEvent>("19-switch-to-luran.json");
  const refusal = workspaceCalendarError("21-switch-refused-bot-already-in.json");
  const shared = elsewhere.upcoming.find((event) => event.recorded_in) as CalendarEvent;
  const own = elsewhere.upcoming.find((event) => !event.recorded_in) as CalendarEvent;
  const rowOf = (title: string) =>
    rows().find((row) => within(row).queryByText(title) !== null) as HTMLElement;

  it("says where it is recorded, in the server's words, with a Switch here button", () => {
    show(elsewhere);
    const row = rowOf(shared.title);
    expect(within(row).getByText(shared.recorded_in_text as string).closest("p")?.className).toBe("meta-line");
    expect(within(row).getByText("Off")).toBeTruthy();
    expect(within(row).getByRole("button", { name: "Switch here" })).toBeTruthy();
    expect(within(row).getByRole("switch").getAttribute("aria-checked")).toBe("false");
  });

  it("draws no Switch here on a row this workspace records", () => {
    show(elsewhere);
    expect(within(rowOf(own.title)).queryByRole("button", { name: "Switch here" })).toBeNull();
    expect(screen.getAllByRole("button", { name: "Switch here" })).toHaveLength(1);
  });

  it("switches with the same write as the switch, and shows the note that it moved", async () => {
    mocks.override.mockResolvedValue(moved);
    show(elsewhere);
    await userEvent.click(within(rowOf(shared.title)).getByRole("button", { name: "Switch here" }));
    expect(mocks.override).toHaveBeenCalledWith(shared.id, true);
    const row = rowOf(shared.title);
    await waitFor(() => expect(within(row).getByText(moved.moved_text as string)).toBeTruthy());
    expect(within(row).queryByRole("button", { name: "Switch here" })).toBeNull();
    expect(within(row).getByRole("switch").getAttribute("aria-checked")).toBe("true");
  });

  it("shows the refusal when the other workspace's note taker is already in the meeting", async () => {
    mocks.override.mockRejectedValue(new ApiError(refusal.status, refusal.code, refusal.message));
    mocks.get.mockResolvedValue(elsewhere);
    show(elsewhere);
    await userEvent.click(within(rowOf(shared.title)).getByRole("button", { name: "Switch here" }));
    await waitFor(() => expect(screen.getByText(refusal.message)).toBeTruthy());
  });
});
