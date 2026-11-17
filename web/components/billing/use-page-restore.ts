import { useEffect } from "react";

// A button that sends the person to the provider stays "busy" while the browser leaves. If they come back with the
// browser's back button the page is restored from the back/forward cache with that state, and the button would stay
// stuck: `pageshow` with `persisted` says so, and `reset` makes the buttons usable again.
export function usePageRestore(reset: () => void): void {
  useEffect(() => {
    const onShow = (event: PageTransitionEvent) => {
      if (event.persisted) reset();
    };
    window.addEventListener("pageshow", onShow);
    return () => window.removeEventListener("pageshow", onShow);
  }, [reset]);
}
