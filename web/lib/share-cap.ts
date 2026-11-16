// What the share page says up front about the recorded hours. The words are the server's own
// (`hours_cap_message`, app/services.py). Only a person who cannot record at all is locked: hours
// held but set aside for a meeting that is already running (`can_record` false with `seconds_left`
// above 0, the server's `reserve`) are a wait, not a limit, so the page lets them try and shows
// the server's sentence if it refuses.
export type CapUsage = {
  used_seconds: number;
  cap_seconds: number | null;
  can_record?: boolean | null;
  seconds_left?: number | null;
} | null;

const NONE_HELD =
  "You do not have any recorded hours at the moment, so the note taker will not join new meetings. Your notes, transcripts and exports stay as they are.";
const ALL_USED =
  "You have used all of your recorded hours, so the note taker will not join new meetings. Your notes, transcripts and exports stay as they are.";

export function capMessageFor(usage: CapUsage | undefined): string | null {
  if (!usage) return null;
  const setAside = usage.can_record === false && (usage.seconds_left ?? 0) > 0;
  if (setAside) return null;
  const capped =
    usage.can_record === false || (usage.cap_seconds != null && usage.used_seconds >= usage.cap_seconds);
  if (!capped) return null;
  return usage.cap_seconds === 0 ? NONE_HELD : ALL_USED;
}
