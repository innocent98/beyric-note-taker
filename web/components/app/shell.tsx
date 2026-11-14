import type { ReactNode } from "react";
import { TimeZoneProvider } from "@/components/app/time-zone-context";
import { ShellNav } from "@/components/app/shell-nav";
import { IconButton, NavLink } from "@/components/ui";
import { initials } from "@/lib/format";

export type NavSection = "meetings" | "ask" | "tasks" | "projects" | "decisions" | "review" | "team" | "notifications" | "templates" | "vocabulary" | "settings";

export type ShellUser = { name: string | null; email: string };

// The name the sidebar shows: the profile name, else the part of the email before the "@".
function shownName(user: ShellUser): string {
  return user.name?.trim() || user.email.split("@")[0] || user.email;
}

// The owner app's frame: the designer's sidebar on desktop; below 860px the same sidebar is a
// drawer opened from a slim top bar (shell-nav.tsx, lead-designed). `tasksNew` is
// `badges.tasks_new`: the count beside Tasks. Log out is a plain form post to /logout
// (app/logout/route.ts), so it works without JavaScript. `reviewOpen` is `badges.review_open` from
// GET /api/session (null when unknown): the count beside Review. `timeZone` is `user.time_zone` of
// the same answer: the zone every time on the page is written in (null = the browser's own).
// `user` is `user` of the same answer: the name, initials and email at the foot of the sidebar.
export function AppShell({
  current,
  reviewOpen = null,
  tasksNew = null,
  unread = null,
  timeZone = null,
  user = null,
  children,
}: {
  current: NavSection;
  reviewOpen?: number | null;
  tasksNew?: number | null;
  unread?: number | null;
  timeZone?: string | null;
  user?: ShellUser | null;
  children: ReactNode;
}) {
  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <ShellNav>
        <nav className="nav" aria-label="Main">
          <NavLink href="/meetings" label="Meetings" current={current === "meetings"} />
          <NavLink href="/ask" label="Ask Beyric" current={current === "ask"} />
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
          {user ? (
            <>
              <div className="avatar" aria-hidden="true">
                {initials(shownName(user))}
              </div>
              <div className="sidebar-user-text">
                <div className="sidebar-user-name">{shownName(user)}</div>
                <div className="sidebar-user-email">{user.email}</div>
              </div>
            </>
          ) : (
            <div className="sidebar-user-text" />
          )}
          <form method="post" action="/logout" className="inline">
            <IconButton icon="logout" label="Log out" type="submit" />
          </form>
        </div>
      </ShellNav>
      <div className="shell-main">
        <main id="main" className="page">
          <TimeZoneProvider value={timeZone}>{children}</TimeZoneProvider>
        </main>
      </div>
    </div>
  );
}
