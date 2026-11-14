import type { ReactNode } from "react";
import { TimeZoneProvider } from "@/components/app/time-zone-context";
import { ActiveTeamSync } from "@/components/app/active-team-sync";
import { ShellNav } from "@/components/app/shell-nav";
import { TeamNavLink } from "@/components/app/team-nav-link";
import { TeamSwitcher } from "@/components/app/team-switcher";
import { IconButton, NavLink } from "@/components/ui";
import { SHELL_DEFAULT_TEAM, SHELL_MARK } from "@/lib/active-team";
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
// (`multi_team` of the session): the shell then draws the lead-designed team switcher and ONE Teams link (the
// list; from it a team's page opens, and from that page the way back and "Make a team"), and the links to the
// screens that follow the team (Meetings, Tasks, Projects, Decisions, Review, Settings) carry the tab's team.
// The badges beside Tasks and Review count that team only. null (the default, and the switch off): the shell
// is exactly what it was before multi-team.
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
  // The screens that are one team's; a plain link while the switch is off. `live` names the badge that is
  // counted again in the browser when this tab's remembered team is not the one the server rendered.
  const scoped = (href: string, label: string, section: NavSection, badge?: number, live?: "tasks_new" | "review_open") =>
    teams ? (
      <TeamNavLink href={href} label={label} teams={teams} current={current === section} badge={badge} live={live} />
    ) : (
      <NavLink href={href} label={label} current={current === section} badge={badge} />
    );
  return (
    <div className="shell" {...(teams ? { [SHELL_MARK]: user?.email ?? "", [SHELL_DEFAULT_TEAM]: String(teams.find((t) => t.is_default)?.id ?? teams[0]?.id ?? "") } : {})}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <ShellNav topbar={teams ? <TeamSwitcher teams={teams} variant="topbar" /> : null}>
        {teams ? <TeamSwitcher teams={teams} variant="sidebar" /> : null}
        <nav className="nav" aria-label="Main">
          {scoped("/", "Meetings", "meetings")}
          <NavLink href="/ask" label="Ask Beyric" current={current === "ask"} />
          {scoped("/tasks", "Tasks", "tasks", tasksNew ?? 0, "tasks_new")}
          {scoped("/projects", "Projects", "projects")}
          {scoped("/decisions", "Decisions", "decisions")}
          {scoped("/review", "Review", "review", reviewOpen ?? 0, "review_open")}
          {teams ? (
            <NavLink href="/teams" label="Teams" current={current === "teams" || current === "team"} />
          ) : (
            <NavLink href="/team" label="Team" current={current === "team"} />
          )}
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
