import type { ReactNode } from "react";
import { AppLink } from "./link";

// A dropdown that opens without JavaScript (<details>). components/behaviors.tsx closes it on an
// outside click or Escape. Fill it with MenuItem.
export function Menu({ label, children }: { label: string; children: ReactNode }) {
  return (
    <details className="menu">
      <summary className="btn btn-secondary">{label}</summary>
      <div className="menu-panel">{children}</div>
    </details>
  );
}

export function MenuItem({ label, href, sub }: { label: string; href: string; sub?: string }) {
  return (
    <AppLink className="menu-item" href={href}>
      {label}
      {sub ? <small>{sub}</small> : null}
    </AppLink>
  );
}
