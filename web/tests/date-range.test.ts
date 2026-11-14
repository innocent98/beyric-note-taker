import { describe, expect, it } from "vitest";
import { addDays, isDay, isZone, isFiltered, NO_RANGE, plainDay, presets, rangeHeading, rangeProblem, rangeQuery, readRange } from "@/lib/date-range";

describe("date range helpers", () => {
  it("accepts only real days written YYYY-MM-DD", () => {
    for (const ok of ["2026-11-14", "2024-02-29", "1900-01-01", "2200-12-31"]) expect(isDay(ok), ok).toBe(true);
    for (const bad of ["2026-02-30", "2025-02-29", "2026-13-01", "2026-11-7", "11/07/2026", "20261107", "2026-11-07T00:00", "", "abc", "1899-12-31", "2201-01-01"])
      expect(isDay(bad), bad).toBe(false);
  });

  it("says in plain words why two boxes cannot be applied", () => {
    expect(rangeProblem("2026-11-01", "2026-11-07")).toBeNull();
    expect(rangeProblem(null, null)).toBeNull();
    expect(rangeProblem("2026-11-01", null)).toBeNull();
    expect(rangeProblem("2026-11-08", "2026-11-07")).toBe("From must be on or before To.");
    expect(rangeProblem("nope", null)).toMatch(/^From is not a date/);
    expect(rangeProblem(null, "2026-02-30")).toMatch(/^To is not a date/);
  });

  it("reads the range from the address, and ignores a damaged one with a line about why", () => {
    expect(readRange({ from: "2026-11-01", to: "2026-11-07" })).toEqual({ range: { from: "2026-11-01", to: "2026-11-07" }, notice: null, zone: null });
    expect(readRange({ to: "2026-11-07" }).range).toEqual({ from: null, to: "2026-11-07" });
    expect(readRange({})).toEqual({ range: NO_RANGE, notice: null, zone: null });
    expect(readRange({ from: "", to: "" })).toEqual({ range: NO_RANGE, notice: null, zone: null });
    expect(readRange({ from: ["2026-11-01", "2026-12-01"] }).range.from).toBe("2026-11-01");
    const bad = readRange({ from: "garbage", to: "2026-11-07" });
    expect(bad.range).toEqual(NO_RANGE);
    expect(bad.notice).toMatch(/could not be used.*every meeting is shown/);
    expect(readRange({ from: "2026-11-09", to: "2026-11-07" }).notice).toMatch(/From must be on or before To/);
  });

  it("writes the range into the address, and nothing for no range", () => {
    expect(rangeQuery({ from: "2026-11-01", to: "2026-11-07" })).toBe("?from=2026-11-01&to=2026-11-07");
    expect(rangeQuery({ from: null, to: "2026-11-07" })).toBe("?to=2026-11-07");
    expect(rangeQuery(NO_RANGE)).toBe("");
    expect(isFiltered(NO_RANGE)).toBe(false);
    expect(isFiltered({ from: "2026-11-01", to: null })).toBe(true);
  });

  it("counts days on the calendar, across months, years and a leap day", () => {
    expect(addDays("2026-11-14", -6)).toBe("2026-11-08");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
    expect(addDays("2024-03-01", -1)).toBe("2024-02-29");
    expect(addDays("2026-01-03", -5)).toBe("2025-12-29");
  });

  it("makes the quick ranges: today, today and six days before, today and 29 before", () => {
    expect(presets("2026-11-14")).toEqual({
      today: { from: "2026-11-14", to: "2026-11-14" },
      last7: { from: "2026-11-08", to: "2026-11-14" },
      last30: { from: "2026-10-16", to: "2026-11-14" },
    });
  });

  it("words the heading from the range, with the year only when it is not this year", () => {
    const today = "2026-11-14";
    expect(plainDay("2026-11-13", today)).toBe("Fri 13 Nov");
    expect(plainDay("2025-12-30", today)).toBe("Tue 30 Dec 2025");
    expect(rangeHeading(NO_RANGE, today)).toBe("Recent");
    expect(rangeHeading({ from: "2026-11-08", to: "2026-11-14" }, today)).toBe("Meetings from Sun 8 Nov to Sat 14 Nov");
    expect(rangeHeading({ from: "2026-11-14", to: "2026-11-14" }, today)).toBe("Meetings on Sat 14 Nov");
    expect(rangeHeading({ from: "2026-11-08", to: null }, today)).toBe("Meetings from Sun 8 Nov");
    expect(rangeHeading({ from: null, to: "2025-01-02" }, today)).toBe("Meetings up to Thu 2 Jan 2025");
  });

  it("keeps the zone of the address only when it is a real zone, and rangeQuery writes it with a range only", () => {
    expect(isZone("America/New_York")).toBe(true);
    expect(isZone("Africa/Lagos")).toBe(true);
    expect(isZone("UTC")).toBe(true);
    for (const bad of ["Mars/Base", "not a zone", "../etc", "", "x".repeat(70)]) expect(isZone(bad), bad).toBe(false);
    expect(readRange({ from: "2026-11-01", tz: "America/New_York" }).zone).toBe("America/New_York");
    expect(readRange({ from: "2026-11-01", tz: "Mars/Base" }).zone).toBeNull();
    expect(readRange({ from: "garbage", tz: "America/New_York" }).zone).toBeNull();
    expect(rangeQuery({ from: "2026-11-01", to: "2026-11-07" }, "America/New_York")).toBe("?from=2026-11-01&to=2026-11-07&tz=America%2FNew_York");
    expect(rangeQuery(NO_RANGE, "America/New_York")).toBe("");
  });
});
