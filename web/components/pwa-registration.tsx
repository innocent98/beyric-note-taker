"use client";

import { useEffect } from "react";

// Registers /sw.js (public/sw.js) so Chrome treats the site as installable. Nothing depends on it
// succeeding: without a service worker the site works the same, it just is not offered for install.
export function PwaRegistration() {
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  }, []);
  return null;
}
