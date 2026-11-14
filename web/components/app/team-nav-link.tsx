"use client";

import { useEffect, useState } from "react";
import { NavLink } from "@/components/ui";
import { withTeam } from "@/lib/active-team";
import { api } from "@/lib/api/browser";
import type { TeamSummary } from "@/lib/api/types";
import { useActiveTeam } from "@/components/app/use-active-team";

// A sidebar link to a screen that follows the team (Meetings, Tasks, Projects, Decisions, Review, Settings): its
// href carries `?team=<id>` when the tab works on a team other than the default. Used by the shell only
// while the multi-team switch is on; otherwise the plain NavLink is drawn.
//
// `live` names a badge (`badges.tasks_new`, `badges.review_open`) that counts one team's items. The server
// renders it for the team of the ADDRESS (`?team=`), else the default team, because that is all it can see;
// a page without `?team=` (the meeting page, Ask) opened in a tab that remembers another team would show the
// wrong number. So when the tab's team (known only in the browser) is not the one the page was drawn for, the
// badge is counted again here, for the tab's team.
export function TeamNavLink({
  href,
  teams,
  badge,
  live,
  ...rest
}: {
  href: string;
  teams: TeamSummary[];
  label: string;
  current?: boolean;
  badge?: number;
  live?: "tasks_new" | "review_open";
  badgeLabel?: string;
}) {
  const { linkId, activeId } = useActiveTeam(teams);
  // The team the server drew this page for: what the first render saw (the address's team, else the default).
  const [drawnFor] = useState(activeId);
  const [counted, setCounted] = useState<{ team: number; value: number } | null>(null);
  const differs = live !== undefined && activeId !== null && activeId !== drawnFor;
  useEffect(() => {
    if (!differs || live === undefined || activeId === null) return;
    let stale = false;
    api
      .session()
      .then((state) => {
        const value = state.badges?.[live];
        if (!stale && value !== undefined) setCounted({ team: activeId, value });
      })
      .catch(() => {
        // Keep the number the server drew.
      });
    return () => {
      stale = true;
    };
  }, [differs, live, activeId]);
  const shown = differs && counted !== null && counted.team === activeId ? counted.value : badge;
  return <NavLink href={withTeam(href, linkId)} badge={shown} {...rest} />;
}
