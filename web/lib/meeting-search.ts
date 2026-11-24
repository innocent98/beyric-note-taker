import { isFiltered, rangeHeading, type DayRange } from "@/lib/date-range";

// The keyword search above the Meetings list (lead-designed, built with the date filter's cards and fields).
// The text is in the address (`/?q=pricing`, beside `from`, `to` and `tz`), so a search can be reloaded and
// shared; it is not sensitive. The server keeps a meeting when its title contains the text, or when every
// word is in its notes or transcript (`GET /api/meetings?q=`): 2 to 100 characters, else 422 `invalid_query`.

export const SEARCH_MIN = 2;
export const SEARCH_MAX = 100;

// The text as the server reads it: trimmed, runs of spaces made one.
export function cleanSearch(text: string): string {
  return text.split(/\s+/).filter(Boolean).join(" ");
}

// Why a text cannot be searched, in plain words; null when it can (or when it is empty: that is no search).
export function searchProblem(text: string): string | null {
  const clean = cleanSearch(text);
  if (clean === "") return null;
  if (clean.length < SEARCH_MIN) return `Type at least ${SEARCH_MIN} characters to search.`;
  if (clean.length > SEARCH_MAX) return `Search can be at most ${SEARCH_MAX} characters.`;
  return null;
}

// The search in the address. A value that cannot be used is left out, and `notice` says so, so a damaged
// link shows every meeting with a line about why, never an error page.
export function readSearch(params: Record<string, string | string[] | undefined>): { q: string | null; notice: string | null } {
  const raw = params.q;
  const text = cleanSearch((Array.isArray(raw) ? raw[0] : raw) ?? "");
  if (text === "") return { q: null, notice: null };
  const problem = searchProblem(text);
  if (problem) return { q: null, notice: `The search in the address could not be used (${problem.replace(/\.$/, "")}), so every meeting is shown.` };
  return { q: text, notice: null };
}

// `query` is "" or "?from=...": the same address with the search added (or without it when there is none).
export function addSearch(query: string, q: string | null): string {
  if (!q) return query;
  const params = new URLSearchParams(query.startsWith("?") ? query.slice(1) : query);
  params.set("q", q);
  return `?${params.toString()}`;
}

// The list's heading while a search is on: "Meetings matching "x"", with the date range's own words first.
export function searchHeading(range: DayRange, today: string | null, q: string): string {
  return isFiltered(range) ? `${rangeHeading(range, today)} matching "${q}"` : `Meetings matching "${q}"`;
}
