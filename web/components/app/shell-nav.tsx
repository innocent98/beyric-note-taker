"use client";

import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";
import { Icon, Logo } from "@/components/ui";

// The phone and tablet navigation. LEAD-DESIGNED, not in the designer's file (owner's request
// 2026-11-07): the designer drew the desktop sidebar only. Below 860px (components.css) the
// sidebar is an off-canvas drawer: a slim top bar carries the logo and a menu button; the button
// opens the drawer from the left over a scrim. On desktop none of this shows and the sidebar is
// the designer's, unchanged.
//
// Open means: focus moves into the drawer and stays there (Tab wraps), the page behind is inert
// and does not scroll, and the drawer is a labelled modal dialog. It closes on Escape, a click on
// the scrim, a click on a link, a route change, or when the window grows past the breakpoint;
// focus then returns to the menu button. Motion is switched off by prefers-reduced-motion in CSS.
const DRAWER_QUERY = "(max-width: 860px)";
const FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

export function ShellNav({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  // The page the drawer was opened on: a route change makes it differ, which closes the drawer
  // without an effect (no flash of a drawer left open on the next page).
  const [openAt, setOpenAt] = useState<string | null>(null);
  const open = openAt === pathname;
  const menuRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const wasOpen = useRef(false);

  const close = () => setOpenAt(null);

  // Focus: into the drawer on open, back to the menu button on close.
  useEffect(() => {
    if (open) {
      closeRef.current?.focus();
    } else if (wasOpen.current) {
      menuRef.current?.focus();
    }
    wasOpen.current = open;
  }, [open]);

  // While open: Escape closes, Tab stays inside, the page behind is inert and does not scroll.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpenAt(null);
        return;
      }
      if (event.key !== "Tab") return;
      const items = Array.from(drawerRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (!drawerRef.current?.contains(active)) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);

    const behind = Array.from(document.querySelectorAll<HTMLElement>(".shell-main, .skip-link, .topbar"));
    behind.forEach((element) => element.setAttribute("inert", ""));

    // Hiding the scrollbar must not move the page: keep its width as padding.
    const { overflow, paddingRight } = document.body.style;
    const scrollbar = window.innerWidth - document.documentElement.clientWidth;
    document.body.style.overflow = "hidden";
    if (scrollbar > 0) document.body.style.paddingRight = `${scrollbar}px`;

    return () => {
      document.removeEventListener("keydown", onKey);
      behind.forEach((element) => element.removeAttribute("inert"));
      document.body.style.overflow = overflow;
      document.body.style.paddingRight = paddingRight;
    };
  }, [open]);

  // Grown past the breakpoint (a tablet turned sideways, a window resized): the sidebar is back
  // as a sidebar, so the drawer state must not linger.
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(DRAWER_QUERY);
    const onChange = () => {
      if (!query.matches) setOpenAt(null);
    };
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  const onDrawerClick = (event: ReactMouseEvent) => {
    if (event.target instanceof Element && event.target.closest("a[href]")) close();
  };

  return (
    <>
      <div className="topbar">
        <button
          ref={menuRef}
          type="button"
          className="topbar-menu"
          aria-label="Open menu"
          aria-expanded={open}
          aria-controls="app-sidebar"
          onClick={() => setOpenAt(pathname)}
        >
          <Icon name="menu" size={20} />
        </button>
        <Logo />
      </div>
      <div className="drawer-scrim" data-open={open ? "true" : "false"} aria-hidden="true" onClick={close} />
      <aside
        ref={drawerRef}
        id="app-sidebar"
        className="sidebar"
        data-open={open ? "true" : "false"}
        {...(open ? { role: "dialog", "aria-modal": true, "aria-label": "Main menu" } : {})}
        onClick={onDrawerClick}
      >
        <div className="sidebar-brand">
          <Logo />
          <button ref={closeRef} type="button" className="drawer-close" aria-label="Close menu" onClick={close}>
            <Icon name="close" size={18} />
          </button>
        </div>
        {children}
      </aside>
    </>
  );
}
