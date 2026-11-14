"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { isTeamPage, parseTeamId, readStoredTeam, TEAM_EVENT, TEAM_PARAM } from "@/lib/active-team";
import type { TeamSummary } from "@/lib/api/types";

// The team this TAB is working on, as the shell's pieces need it (multi-team stage 3): the `?team=` of a
// team page, else the choice remembered in this tab's sessionStorage (written by the switcher), else the
// person's default team. `stored` starts as null and is read after mount, so the first client render
// equals the server's (which only sees the URL). A remembered team the person is no longer in counts
// as no choice (`stale` says so, for the notice).
export function useActiveTeam(teams: TeamSummary[]) {
  const pathname = usePathname();
  const params = useSearchParams();
  const [stored, setStored] = useState<number | null>(null);
  useEffect(() => {
    const read = () => setStored(readStoredTeam());
    read();
    window.addEventListener(TEAM_EVENT, read);
    return () => window.removeEventListener(TEAM_EVENT, read);
  }, []);
  const fromUrl = isTeamPage(pathname) ? parseTeamId(params?.get(TEAM_PARAM)) : null;
  const known = (id: number | null) => (id !== null && teams.some((t) => t.id === id) ? id : null);
  const defaultId = teams.find((t) => t.is_default)?.id ?? teams[0]?.id ?? null;
  const choice = known(fromUrl) ?? known(stored);
  return {
    activeId: choice ?? defaultId,
    // The team to put in a link, null when it is the default (a link without `?team=` is the default team).
    linkId: choice !== null && choice !== defaultId ? choice : null,
    defaultId,
    fromUrl,
    stored,
    stale: stored !== null && known(stored) === null,
    pathname,
  };
}
