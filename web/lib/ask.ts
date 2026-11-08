import type { AskCite } from "@/lib/api/types";

// Set when a source chip on Ask Beyric is clicked: the path it opens. If that meeting answers 404
// (deleted, or unshared since), the not-found page reads it once and says "That meeting is no longer
// available" (spec 1.6, the designer's toast at D:1709). A toast does not survive a page change, so
// it travels in sessionStorage, as DELETED_FLAG does.
export const ASK_CHIP_FLAG = "beyric:ask-chip";

export const ASK_GONE_MESSAGE = "That meeting is no longer available";

// "{title} · {date_label}", plus " · {owner_name}" when the meeting is not the asker's (the API sets
// `owner_name` only then; spec 1.6, the owner part is lead-designed).
export function citeLabel(cite: AskCite): string {
  return [cite.title, cite.date_label, cite.owner_name].filter((part): part is string => Boolean(part)).join(" · ");
}

// A transcript cite opens the Transcript tab at its row; a notes cite opens the notes.
export function citeHref(cite: AskCite): string {
  const base = `/meetings/${cite.meeting_id}`;
  if (cite.target !== "transcript") return base;
  return cite.line !== null && cite.line >= 1 ? `${base}?tab=transcript&line=${cite.line}` : `${base}?tab=transcript`;
}

// The `line` parameter of the meeting page: a 1-based row number, or undefined for anything else.
export function parseLine(raw: string | string[] | undefined): number | undefined {
  const value = Array.isArray(raw) ? raw[0] : raw;
  if (!value || !/^\d{1,6}$/.test(value)) return undefined;
  const line = Number(value);
  return line >= 1 ? line : undefined;
}
