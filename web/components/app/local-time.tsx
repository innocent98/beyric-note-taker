"use client";

import { useSyncExternalStore } from "react";
import { useStoredTimeZone } from "@/components/app/time-zone-context";
import { browserTimeZone, formatDay, formatTime, SERVER_ZONE } from "@/lib/format";

const subscribe = () => () => {};

// The zone every date on the page is written in: the one chosen on Settings, which the server knows
// (so it needs no swap), else the browser's own once the page is in the browser, and UTC until then.
// Anything that has to agree with a `LocalTime` on the same page (the day headings of the Meetings
// list) asks here, so the two can never use different zones.
export function useDisplayTimeZone(): string {
  const inBrowser = useSyncExternalStore(subscribe, () => true, () => false);
  const stored = useStoredTimeZone();
  return stored ?? (inBrowser ? browserTimeZone() : SERVER_ZONE);
}

// A date or time from the API, shown in the viewer's own time zone, as `<time datetime="...">`.
//
// The server renders the page in UTC (it does not know the viewer's zone), and the browser's first
// render has to say the same thing or React reports a hydration mismatch. `useSyncExternalStore`
// does that: while the page is being hydrated it answers `false` (the server's answer), and right
// after it answers `true` and the text is written again in the browser's zone. So the page is
// always correct in UTC first and in the person's zone a moment later, and nothing is warned about.
//
// When the person chose a zone on Settings (`user.time_zone`) that zone is used everywhere, on the
// server render too.
//
// `kind="datetime"` is "02 Nov 2026, 22:16 GMT+1" and `kind="day"` is "Mon 2 Nov 2026" (no zone
// name: a day carries none, as the Jinja pages had it).
export function LocalTime({ iso, kind = "datetime" }: { iso: string; kind?: "datetime" | "day" }) {
  const timeZone = useDisplayTimeZone();
  return <time dateTime={iso}>{kind === "day" ? formatDay(iso, timeZone) : formatTime(iso, timeZone)}</time>;
}
