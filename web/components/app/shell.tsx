import type { ReactNode } from "react";
import { IconButton, Logo, NavLink } from "@/components/ui";

export type NavSection = "meetings" | "decisions" | "review" | "templates" | "vocabulary";

// The owner app's frame: a sidebar on desktop, a top bar on phones (components.css). Log out is a
// plain form post to /logout (app/logout/route.ts), so it works without JavaScript. `reviewOpen` is
// `badges.review_open` from GET /api/session (null when unknown): the count beside Review.
export function AppShell({
  current,
  reviewOpen = null,
  children,
}: {
  current: NavSection;
  reviewOpen?: number | null;
  children: ReactNode;
}) {
  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="sidebar-brand">
          <Logo />
        </div>
        <nav className="nav" aria-label="Main">
          <NavLink href="/" label="Meetings" current={current === "meetings"} />
          <NavLink href="/decisions" label="Decisions" current={current === "decisions"} />
          <NavLink href="/review" label="Review" current={current === "review"} badge={reviewOpen ?? 0} />
        </nav>
        <div className="nav-label">Configure</div>
        <nav className="nav" aria-label="Configure">
          <NavLink href="/templates" label="Templates" current={current === "templates"} />
          <NavLink href="/vocabulary" label="Vocabulary" current={current === "vocabulary"} />
        </nav>
        <div className="sidebar-user">
          <div className="avatar" aria-hidden="true">
            O
          </div>
          <div className="sidebar-user-text">
            <div className="sidebar-user-name">Owner</div>
          </div>
          <form method="post" action="/logout" className="inline">
            <IconButton icon="logout" label="Log out" type="submit" />
          </form>
        </div>
      </aside>
      <div className="shell-main">
        <main id="main" className="page">
          {children}
        </main>
      </div>
    </div>
  );
}
