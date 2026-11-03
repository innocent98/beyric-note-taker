"use client";

import { useCallback, useEffect, useState } from "react";

// A countdown in whole seconds for a "resend in 0:42" label. `start` restarts it.
export function useCountdown(initial: number) {
  const [left, setLeft] = useState(initial);
  const running = left > 0;
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setLeft((value) => Math.max(0, value - 1)), 1000);
    return () => clearInterval(timer);
  }, [running]);
  const start = useCallback((seconds: number) => setLeft(seconds), []);
  return { left, start, label: `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` };
}
