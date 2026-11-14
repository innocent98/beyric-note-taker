"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";
import { Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { isLeavingForTeamSwitch, NOTICE_PARAM, planSync, TEAM_GONE_MESSAGE, writeStoredTeam } from "@/lib/active-team";
import type { TeamSummary } from "@/lib/api/types";
import { useActiveTeam } from "@/components/app/use-active-team";

// Keeps this tab's team in step with the page, and says so when a team is gone. Draws nothing but the
// notice. Rendered by the shell only while the server's multi-team switch is on.
//  - A team page opened with `?team=` remembers it (this tab's sessionStorage), so the pages without the
//    parameter (the meeting page) work on the same team.
//  - A team page opened WITHOUT it while a team is remembered (a link from inside a page, a bookmark) is
//    replaced by the same page on that team, so the person never reads the default team's data thinking
//    it is the other's. (The sidebar's own links carry the parameter, so this is the exception.)
//  - A remembered team the person is no longer in is forgotten, with the plain notice. The server does the
//    same for a `?team=` it cannot find: it redirects with `?notice=team-gone`, shown here once.
export function ActiveTeamSync({ teams }: { teams: TeamSummary[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { stored } = useActiveTeam(teams);
  const [toast, flash] = useToast(7000);

  useEffect(() => {
    // The switcher has started loading another team's page: this page is on its way out and its address
    // names the old team, so nothing here may write the team (see beginTeamSwitch).
    if (isLeavingForTeamSwitch()) return;
    const step = planSync({ pathname, params, teams, stored });
    if (step.kind === "notice") {
      writeStoredTeam(null);
      flash(TEAM_GONE_MESSAGE);
      // Take the notice out of the address, so a reload does not show it again.
      const url = new URL(window.location.href);
      url.searchParams.delete(NOTICE_PARAM);
      window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
    } else if (step.kind === "stale") {
      writeStoredTeam(null);
      flash(TEAM_GONE_MESSAGE);
    } else if (step.kind === "remember") {
      writeStoredTeam(step.teamId);
    } else if (step.kind === "follow") {
      router.replace(step.href);
    }
  }, [stored, teams, pathname, params, router, flash]);

  return <Toast message={toast} />;
}
