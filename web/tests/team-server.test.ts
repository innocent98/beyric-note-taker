import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getShellTeams, getTeams, requireTeamOwner } from "@/lib/api/server";
import type { SessionState, TeamList } from "@/lib/api/types";
import { teamsBody } from "./helpers";

// How a team page learns its team on the server: `?team=` read against the person's own teams
// (capture 46 is Pat's: personal space 2, Platform team 4, Client Y 5); the header goes to FastAPI only on
// the team-scoped calls; with the switch off nothing of this happens. A team that is not theirs is a
// redirect to the same page with the plain notice, never an error page.

vi.mock("next/headers", () => ({ headers: async () => new Headers({ cookie: "session=placeholder" }) }));
vi.mock("next/navigation", () => ({
  redirect: (to: string) => {
    throw new Error(`redirect:${to}`);
  },
  notFound: () => {
    throw new Error("not-found");
  },
}));

const session = (multi_team: boolean, authenticated = true) => ({ ...teamsBody<SessionState>("02-session-ada.json"), multi_team, authenticated });
const teams = teamsBody<TeamList>("46-teams-list.json");

type Seen = { path: string; team: string | null };
let seen: Seen[] = [];
function serve(state: { session: unknown; teams?: unknown; teamsStatus?: number }) {
  seen = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input));
      seen.push({ path: url.pathname, team: (init.headers as Record<string, string>)["X-Team-Id"] ?? null });
      const body = url.pathname === "/api/session" ? state.session : url.pathname === "/api/teams" ? state.teams : { id: 1 };
      const status = url.pathname === "/api/teams" ? (state.teamsStatus ?? 200) : 200;
      return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    }),
  );
}
afterEach(() => vi.unstubAllGlobals());
beforeEach(() => serve({ session: session(true), teams }));

describe("requireTeamOwner", () => {
  it("names the team of ?team= on the team-scoped calls, and only on those", async () => {
    const { api, teams: list, teamId } = await requireTeamOwner("/tasks", { team: "4" });
    expect(teamId).toBe(4);
    expect(list?.teams.map((t) => t.id)).toEqual([2, 4, 5]);
    await api.team.get();
    await api.settings.email.get();
    const calls = seen.filter((s) => !["/api/session", "/api/teams"].includes(s.path));
    expect(calls).toEqual([
      { path: "/api/team", team: "4" },
      { path: "/api/settings/email", team: null },
    ]);
  });

  it("without ?team= it asks for no team (the default team)", async () => {
    const { api, teamId } = await requireTeamOwner("/tasks", {});
    expect(teamId).toBeNull();
    await api.team.get();
    expect(seen.find((s) => s.path === "/api/team")?.team).toBeNull();
  });

  it.each([["99"], ["abc"], ["0"], [["4", "x"]]])("a team that is not theirs (%j) goes to the same page with the notice", async (raw) => {
    // ["4","x"] reads as 4, a team they are in, so it is the one case that passes.
    if (Array.isArray(raw)) {
      expect((await requireTeamOwner("/team", { team: raw })).teamId).toBe(4);
      return;
    }
    await expect(requireTeamOwner("/projects", { team: raw })).rejects.toThrow("redirect:/projects?notice=team-gone");
  });

  it("with the switch off it ignores ?team= completely: no teams call, no header, no redirect", async () => {
    serve({ session: session(false) });
    const { api, teams: list, teamId } = await requireTeamOwner("/tasks", { team: "99" });
    expect(list).toBeNull();
    expect(teamId).toBeNull();
    await api.team.get();
    expect(seen.map((s) => s.path)).not.toContain("/api/teams");
    expect(seen.find((s) => s.path === "/api/team")?.team).toBeNull();
  });

  it("a visitor who is not signed in goes to /login", async () => {
    serve({ session: session(true, false) });
    await expect(requireTeamOwner("/tasks", {})).rejects.toThrow("redirect:/login");
  });
});

describe("the shell's teams", () => {
  it("are the person's, only while the switch is on", async () => {
    expect((await getShellTeams())?.map((t) => t.name)).toEqual(["My team", "Platform team", "Client Y"]);
    serve({ session: session(false) });
    expect(await getShellTeams()).toBeNull();
    expect(seen.map((s) => s.path)).not.toContain("/api/teams");
  });

  it("are left out, not an error, when the list cannot be read", async () => {
    serve({ session: session(true), teams: { error: { code: "boom", message: "x" } }, teamsStatus: 500 });
    expect(await getTeams()).toBeNull();
  });
});
