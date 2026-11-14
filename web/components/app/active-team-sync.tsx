"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";
import { Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import {
  isTeamPage,
  NOTICE_PARAM,
  TEAM_GONE_MESSAGE,
  TEAM_GONE_NOTICE,
  TEAM_PARAM,
  withTeam,
  writeStoredTeam,
} from "@/lib/active-team";
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
  const { fromUrl, stored, stale, defaultId } = useActiveTeam(teams);
  const [toast, flash] = useToast(7000);
  const noticed = params?.get(NOTICE_PARAM) === TEAM_GONE_NOTICE;

  useEffect(() => {
    if (noticed) {
      writeStoredTeam(null);
      flash(TEAM_GONE_MESSAGE);
      // Take the notice out of the address, so a reload does not show it again.
      const url = new URL(window.location.href);
      url.searchParams.delete(NOTICE_PARAM);
      window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
      return;
    }
    if (stale) {
      writeStoredTeam(null);
      flash(TEAM_GONE_MESSAGE);
      return;
    }
    if (fromUrl !== null && teams.some((t) => t.id === fromUrl)) {
      const remembered = fromUrl === defaultId ? null : fromUrl;
      if (remembered !== stored) writeStoredTeam(remembered);
      return;
    }
    if (isTeamPage(pathname) && params?.get(TEAM_PARAM) === null && stored !== null) {
      const query = params.toString();
      router.replace(withTeam(`${pathname}${query ? `?${query}` : ""}`, stored));
    }
  }, [noticed, stale, fromUrl, stored, defaultId, teams, pathname, params, router, flash]);

  return <Toast message={toast} />;
}
