"use client";

import { createContext, useContext, type ReactNode } from "react";

// The zone the person chose on Settings ("Africa/Lagos"), or null while they have not chosen one
// (then the pages use the browser's own zone, as they did before Settings had the row). The owner
// app's shell fills it from GET /api/session (`user.time_zone`) on the server, so the first render
// already writes the chosen zone and nothing waits for the browser.
const StoredTimeZone = createContext<string | null>(null);

export function TimeZoneProvider({ value, children }: { value: string | null; children: ReactNode }) {
  return <StoredTimeZone.Provider value={value}>{children}</StoredTimeZone.Provider>;
}

export const useStoredTimeZone = () => useContext(StoredTimeZone);
