"use client";

import { useEffect, useRef, useState } from "react";

// Reads a one-time token (`?token=`, `?invite=`) from the address bar after the page has mounted,
// then removes it from the address and the history entry, so it is not left in the URL bar, a
// screenshot or the back list. It is read in the browser, not by the server component, so it is not
// in the server-rendered HTML either. A reload of the cleaned address has no token: `token` is then
// null and the screen says the link is missing or used. `ready` is false until the first read.
// The ref keeps the value across React's development double-run of effects (the first run clears
// the address, the second would read nothing).
export function useUrlToken(name: string): { ready: boolean; token: string | null } {
  const read = useRef<string | null | undefined>(undefined);
  const [state, setState] = useState<{ ready: boolean; token: string | null }>({ ready: false, token: null });
  useEffect(() => {
    if (read.current === undefined) {
      const params = new URLSearchParams(window.location.search);
      read.current = params.get(name) || null;
      if (params.has(name)) {
        params.delete(name);
        const rest = params.toString();
        window.history.replaceState(null, "", window.location.pathname + (rest ? `?${rest}` : "") + window.location.hash);
      }
    }
    setState({ ready: true, token: read.current });
  }, [name]);
  return state;
}
