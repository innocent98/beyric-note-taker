// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CalendarBand } from "@/components/app/calendar-upcoming";
import { formatDuration, formatEventDay, formatEventTime } from "@/lib/format";
import type { CalendarState } from "@/lib/api/types";
import { calendarBody } from "./helpers";

// The calendar on the Meetings screen, fed the bodies captured from the running app
// (docs/fe-captures/calendar-state/).

afterEach(cleanup);

const notConnected = calendarBody<CalendarState>("05-state-not-connected.json");
const justConnected = calendarBody<CalendarState>("08-state-connected-before-first-sync.json");
const withEvents = calendarBody<CalendarState>("09-state-connected-with-upcoming.json");
const organizerRule = calendarBody<CalendarState>("10-state-rule-organizer.json");
const noRule = calendarBody<CalendarState>("11-state-rule-none.json");
const needsReconnect = calendarBody<CalendarState>("15-state-needs-reconnect-revoked.json");

// The capture was made on 2026-11-04 at about 13:08 UTC; the list is read at 13:30 UTC that day.
const NOW = new Date("2026-11-04T13:30:00Z");
const show = (state: CalendarState | null, tz = "UTC") =>
  render(<CalendarBand state={state} timeZone={tz} now={NOW} />);
const rows = () => within(screen.getByRole("list", { name: "Upcoming meetings" })).getAllByRole("listitem");

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
    expect(within(first).getByText("15:08")).toBeTruthy();
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
    expect(within(lunch).getByText("Cannot join").getAttribute("title")).toBe(
      "Add a meeting link to this event to record it",
    );
  });

  it("shows Off with its reason on hover, and the designer's three words only", () => {
    show(withEvents);
    const words = rows().map((row) => within(row).getByText(/^(Will join|Off|Cannot join)$/).textContent);
    expect(words).toEqual(["Will join", "Will join", "Cannot join", "Cannot join", "Cannot join", "Will join", "Off"]);
    expect(within(rows()[6]).getByText("Off").getAttribute("title")).toBe("Waiting for you to accept the invite");
    expect(within(rows()[0]).getByText("Will join").getAttribute("title")).toBeNull();
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

  it("draws no switch: the override route does not exist yet", () => {
    show(withEvents);
    expect(screen.queryAllByRole("switch")).toHaveLength(0);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("shows the viewer's own day and time, not UTC", () => {
    show(withEvents, "Africa/Lagos"); // UTC+1: 15:08Z is 16:08
    expect(within(rows()[0]).getByText("16:08")).toBeTruthy();
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
