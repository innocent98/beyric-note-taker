"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// The designer's toast (`toast` in Beyric Note Taker.dc.html): a dark pill, bottom centre, gone after
// a couple of seconds. `useToast` holds the message and the timer; `Toast` draws it. The region is
// always in the page so a screen reader hears a message that arrives later.
export function useToast(ms = 2200): [string, (message: string) => void] {
  const [message, setMessage] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const flash = useCallback(
    (next: string) => {
      clearTimeout(timer.current);
      setMessage(next);
      timer.current = setTimeout(() => setMessage(""), ms);
    },
    [ms],
  );
  return [message, flash];
}

// `inline` draws it where it is placed (the style guide), not fixed to the window.
export function Toast({ message, inline = false }: { message: string; inline?: boolean }) {
  if (inline) return message ? <div className="toast toast-inline">{message}</div> : null;
  return (
    <div className="toast-region" role="status" aria-live="polite">
      {message ? <div className="toast">{message}</div> : null}
    </div>
  );
}
