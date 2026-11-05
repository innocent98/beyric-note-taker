"use client";

import { useSyncExternalStore } from "react";
import { useStoredTimeZone } from "@/components/app/time-zone-context";
import { browserTimeZone, SERVER_ZONE } from "@/lib/format";
import { todayIn } from "@/lib/tasks";

const never = () => () => {};

// Today's date (YYYY-MM-DD) in the person's zone: the zone chosen on Settings, else the browser's.
// The server render and the first browser render say it in UTC (so they agree, as `LocalTime` does)
// and the browser's own answer replaces it once the page is in the browser.
export function useToday(): string {
  const zone = useStoredTimeZone();
  return useSyncExternalStore(
    never,
    () => todayIn(zone ?? browserTimeZone()),
    () => todayIn(zone ?? SERVER_ZONE),
  );
}
