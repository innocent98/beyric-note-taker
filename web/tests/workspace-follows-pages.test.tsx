import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AskPage from "@/app/ask/page";
import DocumentPage from "@/app/documents/[id]/page";
import DocumentsPage from "@/app/documents/page";
import NotificationsPage from "@/app/notifications/page";
import RecipesPage from "@/app/recipes/page";
import { AppShell } from "@/components/app/shell";
import { isTeamPage, withTeam } from "@/lib/active-team";
import type { TeamList } from "@/lib/api/types";
import { teamsBody } from "./helpers";

// The workspace the sidebar switcher names is the workspace Documents, Recipes, Ask, Notifications and a
// draft open on (live bug 2026-11-25: the Documents page showed the default workspace's board in every
// workspace). The server render cannot see the browser's sessionStorage, so these pages must carry
// `?team=` like Tasks does, and the server half must read the page with THAT team's API.

const state = vi.hoisted(() => ({
  asked: [] as { path: string; searchParams: unknown }[],
  calls: [] as string[],
  sync: [] as unknown[],
  doc: { id: 9, title: "A draft", team: { id: 4, name: "Platform team" } } as Record<string, unknown>,
}));
const teams = teamsBody<TeamList>("46-teams-list.json").teams;

vi.mock("next/navigation", () => ({
  usePathname: () => "/documents",
  useSearchParams: () => new URLSearchParams("team=4"),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  redirect: (to: string) => {
    throw new Error(`redirect:${to}`);
  },
  notFound: () => {
    throw new Error("not-found");
  },
}));
vi.mock("@/lib/api/server", () => ({
  // The plain `requireOwner` names no workspace: only a read by id (a draft) may use it. A list page that
  // still did would draw its first read for the default workspace (it has no `list` here, so it fails).
  requireOwner: async () => ({ documents: { get: async () => state.doc } }),
  requireTeamOwner: async (path: string, searchParams: unknown) => {
    state.asked.push({ path, searchParams });
    return {
      teams: null,
      teamId: 4,
      api: {
        documents: { list: async () => (state.calls.push("documents.list"), { documents: [] }), get: async () => state.doc },
        ask: { scope: async () => (state.calls.push("ask.scope"), { meetings: 1, decisions: 0, tasks: 0, suggestions: [] }) },
        notifications: { list: async () => (state.calls.push("notifications.list"), { notifications: [] }) },
        recipes: { list: async () => (state.calls.push("recipes.list"), { recipes: [{ id: null, key: "follow_up", name: "Follow-up", output_type: "email", options: {}, can_edit: false, source: "starter" }], team: { id: 4, name: "Platform team", is_personal: false, can_manage: true } }) },
        brief: { get: async () => (state.calls.push("brief.get"), { text: "", max_chars: 12000, updated_at: null, updated_by: null, team: { id: 4, name: "Platform team", is_personal: false, can_manage: true } }) },
      },
    };
  },
  getReviewOpen: async () => null,
  getTasksNew: async () => 0,
  getUnreadNotifications: async () => null,
  getShellUser: async () => null,
  getShellTeams: async () => teams,
  getTimeZone: async () => null,
  load: <T,>(call: Promise<T>) => call,
}));
vi.mock("@/components/app/documents-view", () => ({ DocumentsView: () => null }));
vi.mock("@/components/app/ask-view", () => ({ AskView: () => null }));
vi.mock("@/components/app/notifications-view", () => ({ NotificationsView: () => null }));
vi.mock("@/components/app/recipes-view", () => ({ RecipesView: () => null }));
vi.mock("@/components/app/document-view", () => ({ DocumentView: () => null }));
vi.mock("@/components/app/meeting-workspace-sync", () => ({
  MeetingWorkspaceSync: (props: unknown) => (state.sync.push(props), null),
}));

beforeEach(() => {
  state.asked = [];
  state.calls = [];
  state.sync = [];
});

describe("the pages that follow the workspace", () => {
  it.each(["/documents", "/recipes", "/ask", "/notifications"])("%s is a team page: the link carries ?team= and the address names the workspace", (path) => {
    expect(isTeamPage(path)).toBe(true);
    expect(withTeam(path, 4)).toBe(`${path}?team=4`);
    expect(withTeam(path, null)).toBe(path);
  });

  it("a single draft is opened by its id, so it is not a team page", () => {
    expect(isTeamPage("/documents/9")).toBe(false);
    expect(withTeam("/documents/9", 4)).toBe("/documents/9");
  });

  it("the sidebar links to Ask, Notifications, Recipes and Documents carry the workspace the tab is on", () => {
    const html = renderToStaticMarkup(
      <AppShell current="documents" teams={teams}>
        x
      </AppShell>,
    );
    for (const path of ["/ask", "/notifications", "/recipes", "/documents"]) expect(html).toContain(`href="${path}?team=4"`);
  });

  it("the Notifications link still shows the unread count", () => {
    const html = renderToStaticMarkup(
      <AppShell current="documents" teams={teams} unread={3}>
        x
      </AppShell>,
    );
    expect(html).toMatch(/href="\/notifications\?team=4"[\s\S]*?3/);
  });
});

describe("the first read of each page is made for the workspace in the address", () => {
  it("Documents reads Mine, and Shared with me, with the team's API", async () => {
    renderToStaticMarkup(await DocumentsPage({ searchParams: Promise.resolve({ team: "4" }) }));
    expect(state.asked).toEqual([{ path: "/documents", searchParams: { team: "4" } }]);
    expect(state.calls).toEqual(["documents.list"]);
    state.calls = [];
    renderToStaticMarkup(await DocumentsPage({ searchParams: Promise.resolve({ team: "4", tab: "shared" }) }));
    expect(state.calls).toEqual(["documents.list"]);
  });

  it("Recipes reads the recipe list and the Team brief with the team's API", async () => {
    renderToStaticMarkup(await RecipesPage({ searchParams: Promise.resolve({ team: "4" }) }));
    expect(state.asked).toEqual([{ path: "/recipes", searchParams: { team: "4" } }]);
    expect(state.calls.sort()).toEqual(["brief.get", "recipes.list"]);
  });

  it("Ask reads its scope with the team's API", async () => {
    renderToStaticMarkup(await AskPage({ searchParams: Promise.resolve({ team: "4" }) }));
    expect(state.asked).toEqual([{ path: "/ask", searchParams: { team: "4" } }]);
    expect(state.calls).toEqual(["ask.scope"]);
  });

  it("Notifications reads its list with the team's API", async () => {
    renderToStaticMarkup(await NotificationsPage({ searchParams: Promise.resolve({ team: "4" }) }));
    expect(state.asked).toEqual([{ path: "/notifications", searchParams: { team: "4" } }]);
    expect(state.calls).toEqual(["notifications.list"]);
  });
});

// Following a remembered workspace swaps the address without a reload (components/app/active-team-sync.tsx), so
// a view that keeps its list in state would keep the OLD workspace's rows: each view is keyed by the workspace.
describe("a view is rebuilt when the workspace changes", () => {
  const keyOf = (tree: unknown) => String(((tree as { props: { children: { key: unknown } } }).props.children as { key: unknown }).key);
  it.each([
    ["Documents", () => DocumentsPage({ searchParams: Promise.resolve({ team: "4" }) })],
    ["Recipes", () => RecipesPage({ searchParams: Promise.resolve({ team: "4" }) })],
    ["Ask", () => AskPage({ searchParams: Promise.resolve({ team: "4" }) })],
    ["Notifications", () => NotificationsPage({ searchParams: Promise.resolve({ team: "4" }) })],
  ])("%s carries the workspace in its key", async (_name, page) => {
    expect(keyOf(await page())).toContain("4");
  });
});

describe("a draft opened by its id", () => {
  it("moves the tab to the workspace the draft was made in, like a meeting does", async () => {
    renderToStaticMarkup(await DocumentPage({ params: Promise.resolve({ id: "9" }) }));
    expect(state.sync).toEqual([{ workspace: { id: 4, name: "Platform team" }, teams }]);
  });

  it("a draft with no workspace on it moves nothing", async () => {
    state.doc = { id: 9, title: "A draft", team: null };
    renderToStaticMarkup(await DocumentPage({ params: Promise.resolve({ id: "9" }) }));
    expect(state.sync).toEqual([{ workspace: null, teams }]);
  });
});
