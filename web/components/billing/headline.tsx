"use client";

import { useEffect, useRef, type ReactNode } from "react";

// The line a card that has just appeared opens with. Focus moves to it, so a keyboard or screen reader user lands on
// the new question or answer instead of on a button that is gone.
export function Headline({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return (
    <strong ref={ref} tabIndex={-1}>
      {children}
    </strong>
  );
}
