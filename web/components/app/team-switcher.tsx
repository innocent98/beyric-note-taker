"use client";

import { useSearchParams } from "next/navigation";
import { useId } from "react";
import { beginTeamSwitch, planSwitch, writeStoredTeam } from "@/lib/active-team";
import type { TeamSummary } from "@/lib/api/types";
import { useActiveTeam } from "@/components/app/use-active-team";

// LEAD-DESIGNED, not in the designer's file (owner's request 2026-11-14, multi-team stage 3; for the
// designer to review): the team switcher. One native select, drawn with the app's own `.select`: in the
// sidebar on desktop, in the slim top bar on phones (`variant`). It lists the person's teams (the personal
// space is labelled "(personal)") and chooses which team the Meetings, Tasks, Projects, Decisions, Review, Team and
// Settings screens work on. A native select is keyboard and screen-reader usable everywhere and opens the system picker on a phone.
//
// The choice belongs to this tab: on a team screen it goes into the URL (`?team=`, which the server render
// reads); on any other page it is remembered in this tab's sessionStorage (lib/active-team.ts). Choosing the
// default team forgets the choice (a link without `?team=` is the default team). Nothing is stored on the server.
//
// Choosing a team is a FULL page load (owner's request 2026-11-14): the browser loads the page again, on the new
// team's address, so every screen, count and cached picker reads again for that team and nothing of the old team
// is left on screen. On a page that is not one team's (the meeting page, Ask) the same page loads again.

// How the switcher loads a page: an object, so a test can replace it (jsdom cannot navigate).
export const pageLoad = { to: (href: string) => window.location.assign(href) };

export function TeamSwitcher({ teams, variant }: { teams: TeamSummary[]; variant: "sidebar" | "topbar" }) {
  const id = useId();
  const search = useSearchParams();
  const { activeId, defaultId, pathname } = useActiveTeam(teams);

  function choose(value: string) {
    const next = Number(value);
    if (!teams.some((t) => t.id === next)) return;
    const { remember, href } = planSwitch({ pathname, query: search?.toString() ?? "", teamId: next, defaultId });
    // The page on screen still names the old team until the new one loads: ActiveTeamSync must leave the
    // choice alone meanwhile (lib/active-team.ts beginTeamSwitch).
    beginTeamSwitch();
    writeStoredTeam(remember);
    pageLoad.to(href);
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
