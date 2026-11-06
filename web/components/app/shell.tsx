import type { ReactNode } from "react";
import { TimeZoneProvider } from "@/components/app/time-zone-context";
import { IconButton, Logo, NavLink } from "@/components/ui";

export type NavSection = "meetings" | "tasks" | "projects" | "decisions" | "review" | "team" | "notifications" | "templates" | "vocabulary" | "settings";

// The owner app's frame: a sidebar on desktop, a top bar on phones (components.css). Log out is a
// `tasksNew` is `badges.tasks_new`: the count beside Tasks.
// plain form post to /logout (app/logout/route.ts), so it works without JavaScript. `reviewOpen` is
// `badges.review_open` from GET /api/session (null when unknown): the count beside Review.
// `timeZone` is `user.time_zone` of the same answer: the zone every time on the page is written in
// (null = the browser's own).
export function AppShell({
  current,
  reviewOpen = null,
  tasksNew = null,
  unread = null,
  timeZone = null,
  children,
}: {
  current: NavSection;
  reviewOpen?: number | null;
  tasksNew?: number | null;
  unread?: number | null;
  timeZone?: string | null;
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
          <NavLink href="/tasks" label="Tasks" current={current === "tasks"} badge={tasksNew ?? 0} />
          <NavLink href="/projects" label="Projects" current={current === "projects"} />
          <NavLink href="/decisions" label="Decisions" current={current === "decisions"} />
          <NavLink href="/review" label="Review" current={current === "review"} badge={reviewOpen ?? 0} />
          <NavLink href="/team" label="Team" current={current === "team"} />
          <NavLink href="/notifications" label="Notifications" current={current === "notifications"} badge={unread ?? 0} badgeLabel="unread" />
        </nav>
        <div className="nav-label">Configure</div>
        <nav className="nav" aria-label="Configure">
          <NavLink href="/templates" label="Templates" current={current === "templates"} />
          <NavLink href="/vocabulary" label="Vocabulary" current={current === "vocabulary"} />
          <NavLink href="/settings" label="Settings" current={current === "settings"} />
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
          <TimeZoneProvider value={timeZone}>{children}</TimeZoneProvider>
        </main>
      </div>
    </div>
  );
}
