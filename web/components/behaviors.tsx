"use client";

import { useEffect } from "react";

// The small amount of JavaScript the shared components need, as delegated listeners (same as
// app/static/app.js on the Jinja pages): a button with data-dialog-open="<id>" opens that <dialog>,
// data-dialog-close closes the dialog it sits in, a click on the backdrop closes it, and a
// details.menu closes on an outside click or Escape. Pages still render without it.
export function Behaviors() {
  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (!target) return;
      const opener = target.closest<HTMLElement>("[data-dialog-open]");
      if (opener) {
        const dialog = document.getElementById(opener.dataset.dialogOpen ?? "");
        if (dialog instanceof HTMLDialogElement) dialog.showModal();
        return;
      }
      const closer = target.closest<HTMLElement>("[data-dialog-close]");
      if (closer) {
        closer.closest("dialog")?.close();
        return;
      }
      if (target instanceof HTMLDialogElement) target.close();
      document.querySelectorAll<HTMLDetailsElement>("details.menu[open]").forEach((menu) => {
        if (!menu.contains(target)) menu.removeAttribute("open");
      });
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      document
        .querySelectorAll<HTMLDetailsElement>("details.menu[open]")
        .forEach((menu) => menu.removeAttribute("open"));
    };
    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, []);
  return null;
}
