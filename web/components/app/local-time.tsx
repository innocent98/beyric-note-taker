"use client";

import { useSyncExternalStore } from "react";
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
// `kind="datetime"` is "02 Nov 2026, 22:16 GMT+1" and `kind="day"` is "Mon 2 Nov 2026" (no zone
// name: a day carries none, as the Jinja pages had it).
export function LocalTime({ iso, kind = "datetime" }: { iso: string; kind?: "datetime" | "day" }) {
  const inBrowser = useSyncExternalStore(subscribe, () => true, () => false);
  const timeZone = inBrowser ? browserTimeZone() : SERVER_ZONE;
  return <time dateTime={iso}>{kind === "day" ? formatDay(iso, timeZone) : formatTime(iso, timeZone)}</time>;
}
