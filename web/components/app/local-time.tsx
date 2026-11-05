"use client";

import { useSyncExternalStore } from "react";
import { useStoredTimeZone } from "@/components/app/time-zone-context";
import { browserTimeZone, formatDay, formatTime, SERVER_ZONE } from "@/lib/format";

const subscribe = () => () => {};

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
  const inBrowser = useSyncExternalStore(subscribe, () => true, () => false);
  // The zone chosen on Settings is known to the server, so it needs no swap; without one it is the
  // browser's.
  const stored = useStoredTimeZone();
  const timeZone = stored ?? (inBrowser ? browserTimeZone() : SERVER_ZONE);
  return <time dateTime={iso}>{kind === "day" ? formatDay(iso, timeZone) : formatTime(iso, timeZone)}</time>;
}
