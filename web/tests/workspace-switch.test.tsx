// @vitest-environment jsdom
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingWorkspaceSync } from "@/components/app/meeting-workspace-sync";
import { TeamSwitcher } from "@/components/app/team-switcher";
import { planMeetingSwitch, readStoredTeam, switchedMessage, TEAM_STORAGE_KEY } from "@/lib/active-team";
import { createApi, TEAM_HEADER } from "@/lib/api/client";
import type { Meeting, SessionState, TeamList } from "@/lib/api/types";
import { teamsBody, workspacesBody } from "./helpers";

// Workspaces foundation, web side (docs/fe-integration-guide-workspaces.md). Every body is a real capture of
// the running API (docs/fe-captures/workspaces/): Pat is in the personal space (id 2, "My team"), Platform team
// (4) and Client Y (5). Nothing here is written by hand except what a test says it changes.

const nav = vi.hoisted(() => ({ path: "/meetings/2", search: "" }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));

const teams = teamsBody<TeamList>("46-teams-list.json").teams;
const sessionX = workspacesBody<SessionState>("06-session-x.json");
const fromAnotherWorkspace = workspacesBody<Meeting>("27-meeting-from-another-workspace.json");
const inActiveWorkspace = workspacesBody<Meeting>("28-meeting-in-active-workspace.json");

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  window.sessionStorage.clear();
  document.body.removeAttribute("data-multi-team");
});
afterEach(() => cleanup());

describe("the header goes on every workspace route", () => {
  // [method path, header] of every call that is not the session read a write makes first for its token.
  type Call = { url: string; init: RequestInit };
  const sent = (calls: Call[]) =>
    calls
      .filter((c) => !(c.url.endsWith("/api/session") && (c.init.method ?? "GET") === "GET"))
      .map((c) => `${c.init.method ?? "GET"} ${c.url}: ${(c.init.headers as Record<string, string>)[TEAM_HEADER]}`);
  function setup() {
    const calls: Call[] = [];
    const fetchMock = vi.fn(async (url: RequestInfo | URL, init: RequestInit = {}) => {
      calls.push({ url: String(url), init });
      return json({ csrf_token: "tok", authenticated: true });
    });
    return { api: createApi({ fetch: fetchMock as unknown as typeof fetch, teamId: () => 4 }), calls };
  }

  it("Ask, templates, vocabulary, notifications, calendar and settings routes name the active workspace", async () => {
    const { api, calls } = setup();
    await api.ask.scope().catch(() => undefined);
    await api.ask.ask("What was said?").catch(() => undefined);
    await api.templates.list().catch(() => undefined);
    await api.vocabulary.list().catch(() => undefined);
    await api.notifications.list().catch(() => undefined);
    await api.calendar.get().catch(() => undefined);
    await api.settings.tasks.get().catch(() => undefined);
    expect(sent(calls)).toEqual([
      "GET /api/ask/scope: 4",
      "POST /api/ask: 4",
      "GET /api/templates: 4",
      "GET /api/vocabulary: 4",
      "GET /api/notifications: 4",
      "GET /api/calendar: 4",
      "GET /api/settings/tasks: 4",
    ]);
  });

  it("the person's own routes (the list of workspaces, the profile settings, sign-in) name none", async () => {
    const { api, calls } = setup();
    await api.teams.list().catch(() => undefined);
    await api.settings.name.get().catch(() => undefined);
    await api.login("a@example.test", "pw").catch(() => undefined);
    expect(sent(calls)).toEqual([
      "GET /api/teams: undefined",
      "GET /api/settings/name: undefined",
      "POST /api/session: undefined",
    ]);
  });
});

describe("the capture the web reads", () => {
  it("names the active workspace and the unread of every other one (the switcher's dots)", () => {
    expect(sessionX.active_workspace).toEqual({ id: 4, name: "Platform team", is_personal: false, kind: "team" });
    expect(sessionX.other_workspaces?.map((w) => [w.id, w.unread_notifications])).toEqual([
      [2, 2],
      [5, 1],
    ]);
  });
  it("a meeting from another workspace names its own, one that is in the active one names the same", () => {
    expect(fromAnotherWorkspace.workspace).toMatchObject({ id: 4, name: "Platform team" });
    expect(inActiveWorkspace.workspace_team_id).toBe(2);
  });
});

describe("opening a meeting of another workspace", () => {
  const platform = { id: 4, name: "Platform team" };

  it("plans a switch with the toast, and remembers 'no choice' for the default workspace", () => {
    const step = planMeetingSwitch({ workspace: platform, activeId: 5, defaultId: 2, teams });
    expect(step).toEqual({ kind: "switch", remember: 4, message: "Switched to Platform team" });
    expect(switchedMessage("Client Y")).toBe("Switched to Client Y");
    expect(planMeetingSwitch({ workspace: { id: 2, name: "My team" }, activeId: 5, defaultId: 2, teams })).toMatchObject({ kind: "switch", remember: null });
  });

  it("does nothing when the meeting is in the workspace the tab is on, or the reader is not in its workspace, or it is unknown", () => {
    expect(planMeetingSwitch({ workspace: platform, activeId: 4, defaultId: 2, teams }).kind).toBe("none");
    expect(planMeetingSwitch({ workspace: platform, activeId: null, defaultId: 4, teams }).kind).toBe("none");
    expect(planMeetingSwitch({ workspace: null, activeId: 5, defaultId: 2, teams }).kind).toBe("none");
    expect(planMeetingSwitch({ workspace: { id: 99, name: "Elsewhere" }, activeId: 5, defaultId: 2, teams }).kind).toBe("none");
  });

  it("switches this tab's stored workspace and shows the toast 'Switched to Platform team'", () => {
    document.body.innerHTML = `<div data-multi-team="pat" data-default-team="2"></div>`;
    window.sessionStorage.setItem(TEAM_STORAGE_KEY, `${personKeyOf("pat")}:5`); // the tab is on Client Y
    render(<MeetingWorkspaceSync workspace={fromAnotherWorkspace.workspace ?? null} teams={teams} />);
    expect(screen.getByRole("status").textContent).toBe("Switched to Platform team");
    expect(readStoredTeam()).toBe(4);
  });

  it("says nothing and keeps the choice when the meeting is already in this tab's workspace", () => {
    document.body.innerHTML = `<div data-multi-team="pat" data-default-team="2"></div>`;
    render(<MeetingWorkspaceSync workspace={inActiveWorkspace.workspace ?? null} teams={teams} />);
    expect(screen.getByRole("status").textContent).toBe("");
    expect(readStoredTeam()).toBeNull();
  });
});

describe("the switcher's unread dots", () => {
  const sidebar = (unread: Record<number, number>) => render(<TeamSwitcher teams={teams} variant="sidebar" unread={unread} />);
  // From the capture where the personal space is the active one: Platform team and Client Y have one unread each.
  const sessionPersonal = workspacesBody<SessionState>("05-session-personal.json");
  const unreadOf = Object.fromEntries((sessionPersonal.other_workspaces ?? []).map((w) => [w.id, w.unread_notifications]));

  it("marks each OTHER workspace that has unread notifications, with its count, and shows one dot", () => {
    document.body.innerHTML = "";
    sidebar(unreadOf);
    const options = within(screen.getByRole("combobox", { name: "Team" })).getAllByRole("option").map((o) => o.textContent);
    // the active workspace here is the default (the personal space): only the others carry a count
    expect(options).toEqual(["My team (personal)", "Platform team (1 unread)", "Client Y (1 unread)"]);
    expect(screen.getByRole("img", { name: /Unread notifications in Platform team, Client Y/ })).toBeTruthy();
  });

  it("draws no dot and no count when nothing is unread elsewhere", () => {
    sidebar({});
    expect(screen.queryByRole("img")).toBeNull();
    expect(within(screen.getByRole("combobox", { name: "Team" })).getAllByRole("option").map((o) => o.textContent)).toEqual(["My team (personal)", "Platform team", "Client Y"]);
  });

  it("never counts the active workspace itself", async () => {
    sidebar({ 2: 5, 4: 1 });
    await act(async () => undefined);
    const texts = within(screen.getByRole("combobox", { name: "Team" })).getAllByRole("option").map((o) => o.textContent);
    expect(texts[0]).toBe("My team (personal)");
  });
});

// The key `writeStoredTeam` uses (lib/active-team.ts personKey): a short hash of the shell mark.
function personKeyOf(mark: string): string {
  let hash = 5381;
  for (const ch of mark) hash = ((hash * 33) ^ ch.charCodeAt(0)) >>> 0;
  return hash.toString(36);
}
