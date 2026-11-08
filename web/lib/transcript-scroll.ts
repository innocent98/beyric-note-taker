"use client";

import { useEffect } from "react";

// How the designer's transcript jump behaves (D:1696-1699): about 300 ms after the row is on the
// page, scroll it into view smoothly with 140 px of room above it (the sticky header and a line of
// context).
export const SCROLL_DELAY_MS = 300;
export const SCROLL_OFFSET_PX = 140;

// Scroll to the transcript row `tl-<line>` once, when `enabled` (the tab is open and the row exists).
export function useScrollToRow(line: number | null, enabled: boolean) {
  useEffect(() => {
    if (!enabled || line === null) return;
    const timer = setTimeout(() => {
      const row = document.getElementById(`tl-${line}`);
      if (row) window.scrollTo({ top: row.getBoundingClientRect().top + window.scrollY - SCROLL_OFFSET_PX, behavior: "smooth" });
    }, SCROLL_DELAY_MS);
    return () => clearTimeout(timer);
  }, [line, enabled]);
}
