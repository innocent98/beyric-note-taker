import type { ReactNode } from "react";
import { TimeZoneProvider } from "@/components/app/time-zone-context";
import { ActiveTeamSync } from "@/components/app/active-team-sync";
import { ShellNav } from "@/components/app/shell-nav";
import { TeamNavLink } from "@/components/app/team-nav-link";
import { TeamSwitcher } from "@/components/app/team-switcher";
import { IconButton, NavLink } from "@/components/ui";
import { SHELL_MARK } from "@/lib/active-team";
import type { TeamSummary } from "@/lib/api/types";
import { initials } from "@/lib/format";

export type NavSection = "meetings" | "ask" | "tasks" | "projects" | "decisions" | "review" | "team" | "teams" | "notifications" | "templates" | "vocabulary" | "settings";

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
// `teams` is the person's teams (`GET /api/teams`), given only while the server's multi-team switch is on
// (`multi_team` of the session): the shell then draws the lead-designed team switcher and a Teams link, and
// the links to the team screens carry the tab's team. null (the default, and the switch off): the shell is
// exactly what it was before multi-team.
export function AppShell({
  current,
  reviewOpen = null,
  tasksNew = null,
  unread = null,
  timeZone = null,
  user = null,
  teams = null,
  children,
}: {
  current: NavSection;
  reviewOpen?: number | null;
  tasksNew?: number | null;
  unread?: number | null;
  timeZone?: string | null;
  user?: ShellUser | null;
  teams?: TeamSummary[] | null;
  children: ReactNode;
}) {
  // The four screens that are one team's; a plain link while the switch is off.
  const scoped = (href: string, label: string, section: NavSection, badge?: number) =>
    teams ? (
      <TeamNavLink href={href} label={label} teams={teams} current={current === section} badge={badge} />
    ) : (
      <NavLink href={href} label={label} current={current === section} badge={badge} />
    );
  return (
    <div className="shell" {...(teams ? { [SHELL_MARK]: user?.email ?? "" } : {})}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <ShellNav topbar={teams ? <TeamSwitcher teams={teams} variant="topbar" /> : null}>
        {teams ? <TeamSwitcher teams={teams} variant="sidebar" /> : null}
        <nav className="nav" aria-label="Main">
          <NavLink href="/" label="Meetings" current={current === "meetings"} />
          <NavLink href="/ask" label="Ask Beyric" current={current === "ask"} />
          {scoped("/tasks", "Tasks", "tasks", tasksNew ?? 0)}
          {scoped("/projects", "Projects", "projects")}
          <NavLink href="/decisions" label="Decisions" current={current === "decisions"} />
          <NavLink href="/review" label="Review" current={current === "review"} badge={reviewOpen ?? 0} />
          {scoped("/team", "Team", "team")}
          {teams ? <NavLink href="/teams" label="Teams" current={current === "teams"} /> : null}
          <NavLink href="/notifications" label="Notifications" current={current === "notifications"} badge={unread ?? 0} badgeLabel="unread" />
        </nav>
        <div className="nav-label">Configure</div>
        <nav className="nav" aria-label="Configure">
          <NavLink href="/templates" label="Templates" current={current === "templates"} />
          <NavLink href="/vocabulary" label="Vocabulary" current={current === "vocabulary"} />
          {scoped("/settings", "Settings", "settings")}
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
        {teams ? <ActiveTeamSync teams={teams} /> : null}
      </div>
    </div>
  );
}
