// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TeamNavLink } from "@/components/app/team-nav-link";
import { TEAM_STORAGE_KEY, activeTeamForRequest, SHELL_MARK, SHELL_DEFAULT_TEAM } from "@/lib/active-team";
import type { TeamList } from "@/lib/api/types";
import { teamsBody } from "./helpers";

// The pieces of "the lists and the counts follow the active team" that live in the browser: the sidebar
// badge counted again for the tab's own team, and the team every browser call names (the default one
// included). Teams are Pat's, from the real capture 46: personal space 2 (default), Platform team 4, Client Y 5.

const nav = vi.hoisted(() => ({ path: "/", search: "" }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useSearchParams: () => new URLSearchParams(nav.search),
}));
const session = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/browser", () => ({ api: { session } }));

const teams = teamsBody<TeamList>("46-teams-list.json").teams;
const at = (path: string, search = "") => {
  nav.path = path;
  nav.search = search;
  window.history.replaceState(null, "", search ? `${path}?${search}` : path);
};
beforeEach(() => {
  window.sessionStorage.clear();
  session.mockReset();
  at("/");
});
afterEach(() => cleanup());

describe("the badge beside Tasks and Review", () => {
  it("is the number the server drew while this tab's team is the one the server drew it for", () => {
    at("/tasks", "team=4");
    render(<TeamNavLink href="/tasks" label="Tasks" teams={teams} badge={7} live="tasks_new" />);
    expect(screen.getByRole("link").textContent).toContain("7");
    expect(session).not.toHaveBeenCalled();
  });

  it("is counted again for the tab's team on a page the server could not know it for (no ?team=)", async () => {
    window.sessionStorage.setItem(TEAM_STORAGE_KEY, ":4");
    at("/ask"); // Ask carries no ?team=: the server drew the default team's number
    session.mockResolvedValue({ badges: { tasks_new: 2, review_open: 0 } });
    render(<TeamNavLink href="/tasks" label="Tasks" teams={teams} badge={41} live="tasks_new" />);
    await waitFor(() => expect(screen.getByRole("link").textContent).toContain("2"));
    expect(screen.getByRole("link").textContent).not.toContain("41");
    expect(session).toHaveBeenCalledTimes(1);
  });

  it("keeps the server's number when the recount fails", async () => {
    window.sessionStorage.setItem(TEAM_STORAGE_KEY, ":4");
    at("/ask");
    session.mockRejectedValue(new Error("offline"));
    render(<TeamNavLink href="/tasks" label="Tasks" teams={teams} badge={5} live="tasks_new" />);
    await waitFor(() => expect(session).toHaveBeenCalled());
    expect(screen.getByRole("link").textContent).toContain("5");
  });

  it("a link with no badge (Projects) never recounts", () => {
    window.sessionStorage.setItem(TEAM_STORAGE_KEY, ":4");
    at("/ask");
    render(<TeamNavLink href="/projects" label="Projects" teams={teams} />);
    expect(session).not.toHaveBeenCalled();
  });
});

describe("the team a browser call names", () => {
  function shell(on: boolean) {
    document.body.innerHTML = on ? `<div ${SHELL_MARK}="pat@example.test" ${SHELL_DEFAULT_TEAM}="2"></div>` : "";
  }
  afterEach(() => (document.body.innerHTML = ""));

  it("is the team the address names on a page that follows the team", () => {
    shell(true);
    at("/tasks", "team=4");
    expect(activeTeamForRequest()).toBe(4);
  });

  it("is the DEFAULT team when the tab has no choice, so every list and count is about one team", () => {
    shell(true);
    at("/");
    expect(activeTeamForRequest()).toBe(2);
  });

  it("is none while the switch is off", () => {
    shell(false);
    expect(activeTeamForRequest()).toBeNull();
  });
});
