import { DAYS, MONTHS, zoned } from "@/lib/format";

// The Meetings list is split by calendar day, in the owner's own time zone (the one `LocalTime`
// writes the row's time in, so a row never sits under a heading that disagrees with its own time).
// A meeting belongs to the day of its `created_at`, the time the row itself shows.
//
// This is the whole rule, kept apart from the component so it can be tested: walk the list in the
// order it came in (newest first) and start a new group whenever the day changes. The list is the
// first page followed by every older page, so a day that runs over a page boundary is one group, not
// two, and a refresh of the first page cannot move a heading: groups are keyed by the day, and a
// meeting only ever lands under the heading of its own day.

export type DayGroup<T> = { key: string; label: string; items: T[] };

// "2026-11-02": the calendar day of an instant in `timeZone`, from numbers (never from words).
// null for a timestamp that is not a date.
export function dayKey(iso: string, timeZone: string): string | null {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const z = zoned(date, timeZone);
  return `${z.year}-${String(z.month).padStart(2, "0")}-${String(z.day).padStart(2, "0")}`;
}

// The calendar day before `key`. Done on the calendar numbers, not by subtracting 24 hours, so a day
// of 23 or 25 hours (a clock change) still has a "yesterday".
function dayBefore(key: string): string {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d - 1)).toISOString().slice(0, 10);
}

// "Today", "Yesterday", else "Mon 2 Nov", with the year ("Mon 2 Nov 2025") only when the day is not
// in the current year. `today` is the key of the current day in the same zone.
export function dayLabel(key: string, today: string): string {
  if (key === today) return "Today";
  if (key === dayBefore(today)) return "Yesterday";
  const [y, m, d] = key.split("-").map(Number);
  const weekday = DAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  const year = y === Number(today.slice(0, 4)) ? "" : ` ${y}`;
  return `${weekday} ${d} ${MONTHS[m - 1]}${year}`;
}

// A meeting whose time cannot be read stays with the group before it (or heads a group of its own
// if it is first), so it is never lost and never splits a day in two.
export const UNKNOWN_DAY = "unknown";

export function groupByDay<T extends { created_at: string }>(
  items: readonly T[],
  timeZone: string,
  now: Date,
): DayGroup<T>[] {
  const today = dayKey(now.toISOString(), timeZone);
  // One group per day, in the order the days first appear. The list is newest first, so a day is
  // normally one unbroken run; if two meetings of a day are ever apart (ids and times can differ by a
  // moment around midnight) they still share one heading, which is why this is a map and not "start
  // a new group when the day changes": a day never has two headings, and React never sees one key twice.
  const groups = new Map<string, DayGroup<T>>();
  let last: DayGroup<T> | undefined;
  for (const item of items) {
    const key = dayKey(item.created_at, timeZone);
    if (key === null) {
      if (!last) {
        last = { key: UNKNOWN_DAY, label: "Date unknown", items: [] };
        groups.set(UNKNOWN_DAY, last);
      }
      last.items.push(item);
      continue;
    }
    let group = groups.get(key);
    if (!group) {
      group = { key, label: dayLabel(key, today ?? key), items: [] };
      groups.set(key, group);
    }
    group.items.push(item);
    last = group;
  }
  return [...groups.values()];
}
