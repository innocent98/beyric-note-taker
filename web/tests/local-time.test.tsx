// @vitest-environment jsdom
import { act, render, screen } from "@testing-library/react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LocalTime } from "@/components/app/local-time";
import { formatDay, formatEventDay, formatTime } from "@/lib/format";

// 21:16 UTC on Mon 2 Nov 2026, and 23:30 UTC the same evening (already Tuesday in Lagos).
const EVENING = "2026-11-02T21:16:52.726490Z";
const LATE = "2026-11-02T23:30:00Z";

describe("formatTime and formatDay in a given zone", () => {
  it("is UTC when no zone is given", () => {
    expect(formatTime(EVENING)).toBe("02 Nov 2026, 21:16 UTC");
    expect(formatDay(EVENING)).toBe("Mon 2 Nov 2026");
  });

  it("writes Lagos as GMT+1", () => {
    expect(formatTime(EVENING, "Africa/Lagos")).toBe("02 Nov 2026, 22:16 GMT+1");
    expect(formatDay(EVENING, "Africa/Lagos")).toBe("Mon 2 Nov 2026");
  });

  it("follows New York's daylight-saving change (clocks went back on 2026-11-01)", () => {
    // 09 Mar 2026 is EDT (UTC-4); 02 Nov 2026 is EST (UTC-5): the same 12:00 UTC reads 08:00, then 07:00.
    expect(formatTime("2026-11-01T05:30:00Z", "America/New_York")).toBe("01 Nov 2026, 01:30 GMT-4");
    expect(formatTime("2026-11-01T06:30:00Z", "America/New_York")).toBe("01 Nov 2026, 01:30 GMT-5");
    expect(formatTime("2026-11-02T12:00:00Z", "America/New_York")).toBe("02 Nov 2026, 07:00 GMT-5");
    expect(formatTime("2026-03-09T12:00:00Z", "America/New_York")).toBe("09 Mar 2026, 08:00 GMT-4");
  });

  it("keeps a half-hour offset", () => {
    expect(formatTime(EVENING, "Asia/Kolkata")).toBe("03 Nov 2026, 02:46 GMT+5:30");
    expect(formatDay(EVENING, "Asia/Kolkata")).toBe("Tue 3 Nov 2026");
  });

  it("moves to the next day in Lagos at 23:30 UTC, and not before", () => {
    expect(formatDay(LATE, "UTC")).toBe("Mon 2 Nov 2026");
    expect(formatDay(LATE, "Africa/Lagos")).toBe("Tue 3 Nov 2026");
    expect(formatTime(LATE, "Africa/Lagos")).toBe("03 Nov 2026, 00:30 GMT+1");
    expect(formatDay("2026-11-02T22:59:00Z", "Africa/Lagos")).toBe("Mon 2 Nov 2026");
    // A year boundary too.
    expect(formatTime("2026-12-31T23:30:00Z", "Africa/Lagos")).toBe("01 Jan 2027, 00:30 GMT+1");
  });

  it("writes September as Sep and midnight as 00:00", () => {
    expect(formatTime("2026-09-15T23:05:00Z", "UTC")).toBe("15 Sep 2026, 23:05 UTC");
    expect(formatTime("2026-09-15T00:00:00Z", "UTC")).toBe("15 Sep 2026, 00:00 UTC");
  });

  it("hands back what it cannot read", () => {
    expect(formatTime("not a date", "Africa/Lagos")).toBe("not a date");
    expect(formatDay("not a date", "Africa/Lagos")).toBe("not a date");
  });

  it("puts the Upcoming day in the same zone as the time (Today and Tomorrow agree with the clock)", () => {
    const now = new Date("2026-11-02T12:00:00Z");
    // 23:30 UTC is today in UTC, but tomorrow in Lagos: the label and the time it sits beside agree.
    expect(formatEventDay(LATE, now, "UTC")).toBe("Today");
    expect(formatEventDay(LATE, now, "Africa/Lagos")).toBe("Tomorrow");
    expect(formatEventDay("2026-11-05T09:00:00Z", now, "Africa/Lagos")).toBe("Thu 5 Nov");
  });
});

describe("LocalTime", () => {
  const original = process.env.TZ;
  beforeEach(() => {
    process.env.TZ = "Africa/Lagos"; // the browser's zone for these tests (Node follows a change at once)
  });
  afterEach(() => {
    process.env.TZ = original;
    vi.restoreAllMocks();
  });

  it("writes the browser's zone into a <time> element that keeps the instant", () => {
    render(<LocalTime iso={EVENING} />);
    const el = screen.getByText("02 Nov 2026, 22:16 GMT+1");
    expect(el.tagName).toBe("TIME");
    expect(el.getAttribute("datetime")).toBe(EVENING);
  });

  it("writes a day with no zone name, and the next day for a late evening", () => {
    render(<LocalTime iso={LATE} kind="day" />);
    expect(screen.getByText("Tue 3 Nov 2026").getAttribute("datetime")).toBe(LATE);
  });

  it("is UTC when the browser will not say its zone", () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({ timeZone: "" } as Intl.ResolvedDateTimeFormatOptions);
    render(<LocalTime iso={EVENING} />);
    expect(screen.getByText("02 Nov 2026, 21:16 UTC")).toBeTruthy();
  });

  it("renders UTC on the server, hydrates with it without a warning, then swaps to the browser's zone", async () => {
    // The server render: no browser, so UTC.
    const html = renderToString(<LocalTime iso={EVENING} />);
    expect(html).toContain("02 Nov 2026, 21:16 UTC");
    expect(html).not.toContain("GMT+1");

    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const container = document.createElement("div");
    container.innerHTML = html;
    document.body.appendChild(container);

    let root!: ReturnType<typeof hydrateRoot>;
    await act(async () => {
      root = hydrateRoot(container, <LocalTime iso={EVENING} />);
    });

    // After hydration (and its follow-up render) the page says the viewer's own zone...
    expect(container.textContent).toBe("02 Nov 2026, 22:16 GMT+1");
    expect(container.querySelector("time")?.getAttribute("datetime")).toBe(EVENING);
    // ...and React reported no mismatch on the way.
    expect(errors).not.toHaveBeenCalled();
    await act(async () => root.unmount());
    container.remove();
  });
});
