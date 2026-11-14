import { activeTeamForRequest, resetToDefaultTeam, writeStoredTeam } from "@/lib/active-team";
import { createApi } from "./client";

// The API as the browser calls it: same origin (nginx and Next pass /api/* to FastAPI), so the
// session cookie travels by itself and a login's Set-Cookie lands where it should. Writes from
// here are what keep working while a slow rewrite runs, see docs/sop/2026-11-03-nextjs-owner-screens.md.
// A 401 anywhere sends the owner to the login page, once, for every screen.
// Team-scoped calls (client.ts `isTeamScoped`) carry the tab's team in `X-Team-Id`: the `?team=` of a
// team page, else the choice remembered in this tab's sessionStorage (lib/active-team.ts); none
// while the server's multi-team switch is off. A 404 "No such team." sends the tab back to the default team.
export const api = createApi({
  teamId: activeTeamForRequest,
  onTeamGone: resetToDefaultTeam,
  // A login or logout here: the next person in this tab starts on their default team.
  onSessionChange: () => writeStoredTeam(null),
  onUnauthorized: () => {
    if (typeof window !== "undefined" && window.location.pathname !== "/login") {
      // A full load, so the server renders the login page fresh.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.href = "/login";
    }
  },
});
