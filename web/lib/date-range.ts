import { DAYS, MONTHS } from "@/lib/format";

// The date filter of the Meetings list (lead-designed). A range is two calendar days, `YYYY-MM-DD`,
// both included, in the owner's own time zone: the same days the server filters by
// (`GET /api/meetings?from=&to=`, docs/fe-integration-guide-owner-app.md 5.1a) and the same day
// headings the list draws. The range lives in the address (`/?from=2026-11-01&to=2026-11-07`)
// so a filtered view can be reloaded and shared; the dates are not sensitive.

export type DayRange = { from: string | null; to: string | null };

export const NO_RANGE: DayRange = { from: null, to: null };

// A real calendar day written exactly YYYY-MM-DD (2026-02-30 is not one), in the years the server accepts.
export function isDay(text: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return false;
  const [y, m, d] = text.split("-").map(Number);
  if (y < 1900 || y > 2200) return false;
  const date = new Date(Date.UTC(y, m - 1, d));
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d;
}

export function isFiltered(range: DayRange): boolean {
  return range.from !== null || range.to !== null;
}

// Why two boxes cannot be applied, in plain words; null when they can.
export function rangeProblem(from: string | null, to: string | null): string | null {
  if (from !== null && !isDay(from)) return "From is not a date. Pick a day from the calendar.";
  if (to !== null && !isDay(to)) return "To is not a date. Pick a day from the calendar.";
  if (from !== null && to !== null && from > to) return "From must be on or before To.";
  return null;
}

// A time zone name the browser's own database knows ("America/New_York"); anything else is not one.
export function isZone(text: string): boolean {
  if (!/^[A-Za-z0-9_+\-/]{1,64}$/.test(text)) return false;
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone: text });
    return true;
  } catch {
    return false;
  }
}

// The range in the address, from `searchParams`. A value that cannot be used is left out, and `notice`
// says so, so a damaged link shows every meeting with a line about why, never an error page.
//
// `zone` is the `tz` of the address: the zone the days were chosen in, kept only while the owner has no
// zone stored on Settings (the page then writes its day headings in the browser's zone, so the server
// must cut the days in that zone too). A `tz` that is not a zone is dropped without a notice.
export function readRange(params: Record<string, string | string[] | undefined>): { range: DayRange; notice: string | null; zone: string | null } {
  const one = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value);
  const from = one(params.from) || null;
  const to = one(params.to) || null;
  const asked = one(params.tz) || null;
  const zone = asked !== null && isZone(asked) ? asked : null;
  const problem = rangeProblem(from, to);
  if (problem) return { range: NO_RANGE, notice: `The dates in the address could not be used (${problem.replace(/\.$/, "")}), so every meeting is shown.`, zone: null };
  return { range: { from, to }, notice: null, zone };
}

// "?from=2026-11-01&to=2026-11-07", "" for no range. `zone` goes on as `tz` only with a range, and only
// when the owner has no stored zone (the caller passes null then).
export function rangeQuery(range: DayRange, zone: string | null = null): string {
  const query = new URLSearchParams();
  if (range.from) query.set("from", range.from);
  if (range.to) query.set("to", range.to);
  if (zone && (range.from || range.to)) query.set("tz", zone);
  const text = query.toString();
  return text ? `?${text}` : "";
}

// The calendar day `n` days from `key`, on the calendar numbers (a day of 23 or 25 hours still counts as one).
export function addDays(key: string, n: number): string {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

// The three quick buttons. `today` is the current day in the owner's zone. "Last 7 days" is today
// and the six days before it, "Last 30 days" today and the 29 before: so each really is that many days.
export function presets(today: string): { today: DayRange; last7: DayRange; last30: DayRange } {
  return {
    today: { from: today, to: today },
    last7: { from: addDays(today, -6), to: today },
    last30: { from: addDays(today, -29), to: today },
  };
}

// "Mon 9 Nov", with the year ("Mon 9 Nov 2025") only when the day is not in the current year. Never
// "Today" or "Yesterday": a heading about a range names its days.
export function plainDay(key: string, today: string | null): string {
  const [y, m, d] = key.split("-").map(Number);
  const weekday = DAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  const year = today !== null && y === Number(today.slice(0, 4)) ? "" : ` ${y}`;
  return `${weekday} ${d} ${MONTHS[m - 1]}${year}`;
}

// The list's heading while a filter is on.
export function rangeHeading(range: DayRange, today: string | null): string {
  const { from, to } = range;
  if (from && to) return from === to ? `Meetings on ${plainDay(from, today)}` : `Meetings from ${plainDay(from, today)} to ${plainDay(to, today)}`;
  if (from) return `Meetings from ${plainDay(from, today)}`;
  if (to) return `Meetings up to ${plainDay(to, today)}`;
  return "Recent";
}
