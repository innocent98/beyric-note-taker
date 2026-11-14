"use client";

import { NavLink } from "@/components/ui";
import { withTeam } from "@/lib/active-team";
import type { TeamSummary } from "@/lib/api/types";
import { useActiveTeam } from "@/components/app/use-active-team";

// A sidebar link to a team screen (Team, Tasks, Projects, Settings) that keeps this tab's team: its
// href carries `?team=<id>` when the tab works on a team other than the default. Used by the shell only
// while the multi-team switch is on; otherwise the plain NavLink is drawn.
export function TeamNavLink({
  href,
  teams,
  ...rest
}: {
  href: string;
  teams: TeamSummary[];
  label: string;
  current?: boolean;
  badge?: number;
  badgeLabel?: string;
}) {
  const { linkId } = useActiveTeam(teams);
  return <NavLink href={withTeam(href, linkId)} {...rest} />;
}
