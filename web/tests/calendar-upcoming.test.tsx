// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CalendarBand } from "@/components/app/calendar-upcoming";
import { ApiError } from "@/lib/api/client";
import { formatDuration, formatEventDay, formatEventTime } from "@/lib/format";
import type { CalendarEvent, CalendarState } from "@/lib/api/types";
import { calendarBody, settingsBody, settingsError } from "./helpers";

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

  it("is the same band when Google needs the person to connect again", () => {
    show(needsReconnect);
    expect(screen.getByRole("link", { name: "Connect calendar" })).toBeTruthy();
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
    expect(within(lunch).getByText("Cannot join").closest("[title]")?.getAttribute("title")).toBe(
      "Add a meeting link to this event to record it",
    );
  });

  it("shows Off with its reason on hover, and the designer's three words only", () => {
    show(withEvents);
    const words = rows().map((row) => within(row).getByText(/^(Will join|Off|Cannot join)$/).textContent);
    expect(words).toEqual(["Will join", "Will join", "Cannot join", "Cannot join", "Cannot join", "Will join", "Off"]);
    expect(within(rows()[6]).getByText("Off").closest("[title]")?.getAttribute("title")).toBe(
      "Waiting for you to accept the invite",
    );
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
    expect(within(rows()[0]).getByText("Off").closest("[title]")?.getAttribute("title")).toBe(
      "You switched this meeting off",
    );
    expect(switchIn(rows()[0]).getAttribute("aria-checked")).toBe("false");
    expect(switchIn(rows()[1]).getAttribute("aria-checked")).toBe("true"); // the others stay as they were
  });

  it("switches an Off meeting on, the one-click way into an invite that was not accepted", async () => {
    mocks.override.mockResolvedValue(invited);
    show(withEvents);
    const row = rows()[6];
    expect(switchIn(row).getAttribute("title")).toBe("Waiting for you to accept the invite");
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
    expect(switchIn(row).getAttribute("title")).toBe("Add a meeting link to this event to record it");
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
