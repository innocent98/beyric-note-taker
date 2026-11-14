"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useId } from "react";
import { isTeamPage, withTeam, writeStoredTeam } from "@/lib/active-team";
import type { TeamSummary } from "@/lib/api/types";
import { useActiveTeam } from "@/components/app/use-active-team";

// LEAD-DESIGNED, not in the designer's file (owner's request 2026-11-14, multi-team stage 3; for the
// designer to review): the team switcher. One native select, drawn with the app's own `.select`: in the
// sidebar on desktop, in the slim top bar on phones (`variant`). It lists the person's teams (the personal
// space is labelled "(personal)") and chooses which team the Team, Tasks, Projects and Settings screens work
// on. A native select is keyboard and screen-reader usable everywhere and opens the system picker on a phone.
//
// The choice belongs to this tab: on a team screen it goes into the URL (`?team=`, which the server render
// reads); on any other page it is remembered in this tab's sessionStorage (lib/active-team.ts). Choosing the
// default team forgets the choice (a link without `?team=` is the default team). Nothing is stored on the server.
export function TeamSwitcher({ teams, variant }: { teams: TeamSummary[]; variant: "sidebar" | "topbar" }) {
  const id = useId();
  const router = useRouter();
  const search = useSearchParams();
  const { activeId, defaultId, pathname } = useActiveTeam(teams);

  function choose(value: string) {
    const next = Number(value);
    if (!teams.some((t) => t.id === next)) return;
    const remembered = next === defaultId ? null : next;
    writeStoredTeam(remembered);
    if (isTeamPage(pathname)) {
      const query = search?.toString();
      router.push(withTeam(`${pathname}${query ? `?${query}` : ""}`, remembered));
    }
  }

  return (
    <div className={`team-switch team-switch-${variant}`}>
      <label className={variant === "sidebar" ? "team-switch-label" : "visually-hidden"} htmlFor={id}>
        Team
      </label>
      <select id={id} className="select select-sm team-switch-select" value={activeId ?? ""} onChange={(event) => choose(event.target.value)}>
        {teams.map((team) => (
          <option key={team.id} value={team.id}>
            {team.is_personal ? `${team.name} (personal)` : team.name}
          </option>
        ))}
      </select>
    </div>
  );
}
