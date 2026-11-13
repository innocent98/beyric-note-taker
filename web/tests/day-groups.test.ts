import { describe, expect, it } from "vitest";
import { dayKey, dayLabel, groupByDay } from "@/lib/day-groups";

// The grouping rule of the Meetings list: one heading per calendar day in the owner's own zone.
// Mon 2 Nov 2026 is a Monday; Fri 13 Nov 2026 is a Friday.
const at = (id: number, iso: string) => ({ id, created_at: iso });
const labels = (groups: { label: string }[]) => groups.map((g) => g.label);
const ids = (groups: { items: { id: number }[] }[]) => groups.map((g) => g.items.map((m) => m.id));
const NOW = new Date("2026-11-13T15:00:00Z"); // Fri 13 Nov 2026, afternoon, in UTC and in Lagos

describe("dayKey", () => {
  it("is the calendar day in the zone, from numbers", () => {
    expect(dayKey("2026-11-02T21:16:52.726490Z", "UTC")).toBe("2026-11-02");
    expect(dayKey("2026-11-02T21:16:52.726490Z", "Africa/Lagos")).toBe("2026-11-02");
    expect(dayKey("2026-11-02T21:16:52.726490Z", "Asia/Kolkata")).toBe("2026-11-03");
  });

  it("is null for something that is not a date", () => {
    expect(dayKey("not a date", "UTC")).toBeNull();
    expect(dayKey("", "UTC")).toBeNull();
  });
});

describe("groupByDay", () => {
  it("puts meetings of one day under one heading, newest first as given", () => {
    const groups = groupByDay(
      [at(3, "2026-11-13T14:00:00Z"), at(2, "2026-11-13T09:30:00Z"), at(1, "2026-11-12T10:00:00Z")],
      "UTC",
      NOW,
    );
    expect(labels(groups)).toEqual(["Today", "Yesterday"]);
    expect(ids(groups)).toEqual([[3, 2], [1]]);
  });

  it("is empty for an empty list", () => {
    expect(groupByDay([], "UTC", NOW)).toEqual([]);
  });

  describe("the midnight edge in a zone that is not UTC", () => {
    // Lagos is UTC+1 all year: 22:59Z is 23:59 and 23:00Z is 00:00 the next day.
    const lagos = [at(2, "2026-11-02T23:00:00Z"), at(1, "2026-11-02T22:59:59Z")];

    it("splits at local midnight in Lagos, one second apart", () => {
      const groups = groupByDay(lagos, "Africa/Lagos", NOW);
      expect(labels(groups)).toEqual(["Tue 3 Nov", "Mon 2 Nov"]);
      expect(ids(groups)).toEqual([[2], [1]]);
    });

    it("keeps the same two meetings together in UTC, where they are one day", () => {
      const groups = groupByDay(lagos, "UTC", NOW);
      expect(labels(groups)).toEqual(["Mon 2 Nov"]);
      expect(ids(groups)).toEqual([[2, 1]]);
    });

    it("splits at local midnight west of UTC too (New York, EST on 2 Nov: 05:00Z is 00:00)", () => {
      const groups = groupByDay([at(2, "2026-11-02T05:00:00Z"), at(1, "2026-11-02T04:59:59Z")], "America/New_York", NOW);
      expect(labels(groups)).toEqual(["Mon 2 Nov", "Sun 1 Nov"]);
    });

    it("follows a clock change: 1 Nov in New York lasts 25 hours (00:00 EDT at 04:00Z to 00:00 EST at 05:00Z the next day)", () => {
      const groups = groupByDay(
        [
          at(4, "2026-11-02T05:00:00Z"), // 00:00 EST, Mon 2 Nov
          at(3, "2026-11-02T04:59:59Z"), // 23:59:59 EST, still Sun 1 Nov
          at(2, "2026-11-01T04:00:00Z"), // 00:00 EDT, Sun 1 Nov
          at(1, "2026-11-01T03:59:59Z"), // 23:59:59 EDT, Sat 31 Oct
        ],
        "America/New_York",
        NOW,
      );
      expect(labels(groups)).toEqual(["Mon 2 Nov", "Sun 1 Nov", "Sat 31 Oct"]);
      expect(ids(groups)).toEqual([[4], [3, 2], [1]]);
    });
  });

  describe("Today and Yesterday", () => {
    it("are decided in the owner's zone, not in UTC", () => {
      // 23:30Z on Fri 13 Nov is already Sat 14 Nov in Lagos.
      const now = new Date("2026-11-13T23:30:00Z");
      const meeting = [at(1, "2026-11-13T12:00:00Z")];
      expect(labels(groupByDay(meeting, "UTC", now))).toEqual(["Today"]);
      expect(labels(groupByDay(meeting, "Africa/Lagos", now))).toEqual(["Yesterday"]);
    });

    it("name the day before Today even across a month, a year and a leap day", () => {
      expect(dayLabel("2026-10-31", "2026-11-01")).toBe("Yesterday");
      expect(dayLabel("2025-12-31", "2026-01-01")).toBe("Yesterday");
      expect(dayLabel("2028-02-29", "2028-03-01")).toBe("Yesterday");
      expect(dayLabel("2026-11-12", "2026-11-14")).toBe("Thu 12 Nov"); // two days back is a date
    });

    it("say a day in the future by its date (a clock that is ahead)", () => {
      expect(dayLabel("2026-11-14", "2026-11-13")).toBe("Sat 14 Nov");
    });
  });

  describe("the year", () => {
    it("is left out for this year and shown for any other", () => {
      expect(dayLabel("2026-11-09", "2026-11-13")).toBe("Mon 9 Nov");
      expect(dayLabel("2026-01-02", "2026-11-13")).toBe("Fri 2 Jan");
      expect(dayLabel("2025-12-30", "2026-11-13")).toBe("Tue 30 Dec 2025");
      expect(dayLabel("2027-01-04", "2026-11-13")).toBe("Mon 4 Jan 2027");
    });

    it("is read in the owner's zone: New Year's Eve evening UTC is already 2026 in Lagos", () => {
      const now = new Date("2026-03-01T12:00:00Z");
      expect(labels(groupByDay([at(1, "2025-12-31T23:30:00Z")], "UTC", now))).toEqual(["Wed 31 Dec 2025"]);
      expect(labels(groupByDay([at(1, "2025-12-31T23:30:00Z")], "Africa/Lagos", now))).toEqual(["Thu 1 Jan"]);
    });
  });

  describe("across pages", () => {
    const first = [at(60, "2026-11-12T18:00:00Z"), at(59, "2026-11-12T12:00:00Z")];
    const second = [at(58, "2026-11-12T08:00:00Z"), at(57, "2026-11-11T20:00:00Z")];

    it("continues a day that runs over the page boundary under the same heading, once", () => {
      const groups = groupByDay([...first, ...second], "UTC", NOW);
      expect(labels(groups)).toEqual(["Yesterday", "Wed 11 Nov"]);
      expect(ids(groups)).toEqual([[60, 59, 58], [57]]);
    });

    it("gives the same groups as grouping each page and joining by day", () => {
      const whole = groupByDay([...first, ...second], "UTC", NOW);
      const firstOnly = groupByDay(first, "UTC", NOW);
      expect(whole[0].key).toBe(firstOnly[0].key);
      expect(whole[0].label).toBe(firstOnly[0].label);
    });

    it("keeps every heading where it was when a new meeting arrives at the top (the live refresh)", () => {
      const before = groupByDay([...first, ...second], "UTC", NOW);
      const refreshed = groupByDay([at(61, "2026-11-13T14:00:00Z"), ...first, ...second], "UTC", NOW);
      expect(labels(refreshed)).toEqual(["Today", ...labels(before)]);
      expect(refreshed.slice(1).map((g) => g.key)).toEqual(before.map((g) => g.key));
    });

    it("does not make a second Today heading when a new meeting arrives on a day already shown", () => {
      const refreshed = groupByDay([at(63, "2026-11-13T14:30:00Z"), at(62, "2026-11-13T14:00:00Z"), ...first], "UTC", NOW);
      expect(labels(refreshed)).toEqual(["Today", "Yesterday"]);
    });
  });

  it("gives a day one heading even if two of its meetings are apart in the list", () => {
    const groups = groupByDay(
      [at(3, "2026-11-13T10:00:00Z"), at(2, "2026-11-12T10:00:00Z"), at(1, "2026-11-13T09:00:00Z")],
      "UTC",
      NOW,
    );
    expect(labels(groups)).toEqual(["Today", "Yesterday"]);
    expect(new Set(groups.map((g) => g.key)).size).toBe(groups.length);
  });

  it("keeps a meeting whose time cannot be read, with the day before it, or alone if first", () => {
    const bad = at(9, "garbage");
    expect(ids(groupByDay([at(3, "2026-11-13T10:00:00Z"), bad, at(1, "2026-11-12T10:00:00Z")], "UTC", NOW))).toEqual([
      [3, 9],
      [1],
    ]);
    const groups = groupByDay([bad, at(1, "2026-11-12T10:00:00Z")], "UTC", NOW);
    expect(labels(groups)).toEqual(["Date unknown", "Yesterday"]);
    expect(ids(groups)).toEqual([[9], [1]]);
  });
});
