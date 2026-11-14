// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ActiveTeamSync } from "@/components/app/active-team-sync";
import { AppShell } from "@/components/app/shell";
import { pageLoad, TeamSwitcher } from "@/components/app/team-switcher";
import { SHELL_MARK, TEAM_STORAGE_KEY } from "@/lib/active-team";
import type { TeamList } from "@/lib/api/types";
import { teamsBody } from "./helpers";

// The lead-designed team switcher, the shell around it, and the sync that keeps the tab's team. Teams
// are Pat's, from the real capture 46: the personal space (the default, id 2), Platform team (4, owner)
// and Client Y (5, member).

const nav = vi.hoisted(() => ({ path: "/", search: "", push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.path,
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ push: nav.push, replace: nav.replace, refresh: nav.refresh }),
}));

const teams = teamsBody<TeamList>("46-teams-list.json").teams;

function at(path: string, search = "") {
  nav.path = path;
  nav.search = search;
  window.history.replaceState(null, "", search ? `${path}?${search}` : path);
}
beforeEach(() => {
  window.dispatchEvent(new Event("pageshow")); // a page that was leaving for a switch is on screen again
  nav.push.mockReset();
  nav.replace.mockReset();
  nav.refresh.mockReset();
  window.sessionStorage.clear();
  at("/");
});
afterEach(() => cleanup());

// The switcher loads the page again (a full load): jsdom cannot, so what it was asked to load is recorded.
const load = vi.spyOn(pageLoad, "to").mockImplementation(() => undefined);
beforeEach(() => load.mockClear());

const select = () => screen.getByRole("combobox", { name: "Team" }) as HTMLSelectElement;

describe("the switcher", () => {
  it("lists every team, labels the personal space, and starts on the default team", () => {
    render(<TeamSwitcher teams={teams} variant="sidebar" />);
    const options = within(select()).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["My team (personal)", "Platform team", "Client Y"]);
    expect(select().value).toBe("2");
  });

  it("is a labelled native select: the label is shown in the sidebar and read by a screen reader in the top bar", () => {
    const { container, unmount } = render(<TeamSwitcher teams={teams} variant="sidebar" />);
    expect(container.querySelector("label.team-switch-label")?.textContent).toBe("Team");
    unmount();
    const bar = render(<TeamSwitcher teams={teams} variant="topbar" />);
    expect(bar.container.querySelector("label.visually-hidden")?.textContent).toBe("Team");
    expect(screen.getByRole("combobox", { name: "Team" })).toBeTruthy();
  });

  it("on a team page the choice goes into the URL, is remembered for this tab, and the page is LOADED again", () => {
    at("/tasks");
    render(<TeamSwitcher teams={teams} variant="sidebar" />);
    fireEvent.change(select(), { target: { value: "4" } });
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBe(":4");
    expect(load).toHaveBeenCalledWith("/tasks?team=4");
    expect(nav.push).not.toHaveBeenCalled(); // never a client-side transition: a full page load
  });

  it.each(["/", "/decisions", "/review"])("switching on %s (a page that follows the team) loads it again on the new team", (path) => {
    at(path);
    render(<TeamSwitcher teams={teams} variant="sidebar" />);
    fireEvent.change(select(), { target: { value: "5" } });
    expect(load).toHaveBeenCalledWith(`${path}?team=5`);
  });

  it("on a page that is not one team's (the meeting page, Ask) the same page is loaded again, the choice remembered", () => {
    at("/meetings/12");
    render(<TeamSwitcher teams={teams} variant="sidebar" />);
    fireEvent.change(select(), { target: { value: "4" } });
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBe(":4");
    expect(load).toHaveBeenCalledWith("/meetings/12");
  });

  it("keeps the rest of the query (the Settings page after the calendar's callback)", () => {
    at("/settings", "calendar=connected");
    render(<TeamSwitcher teams={teams} variant="sidebar" />);
    fireEvent.change(select(), { target: { value: "5" } });
    expect(load).toHaveBeenCalledWith("/settings?calendar=connected&team=5");
  });

  it("choosing the default team forgets the choice and drops ?team=", () => {
    at("/tasks", "team=4");
    window.sessionStorage.setItem(TEAM_STORAGE_KEY, ":4");
    render(<TeamSwitcher teams={teams} variant="sidebar" />);
    expect(select().value).toBe("4");
    fireEvent.change(select(), { target: { value: "2" } });
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBeNull();
    expect(load).toHaveBeenCalledWith("/tasks");
  });

  it("on a page that is not one team's (the Meetings list) it only remembers the choice", () => {
    render(<TeamSwitcher teams={teams} variant="sidebar" />);
    fireEvent.change(select(), { target: { value: "5" } });
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBe(":5");
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("shows the team of the URL, and a remembered team the person is no longer in is ignored", () => {
    at("/projects", "team=5");
    const first = render(<TeamSwitcher teams={teams} variant="sidebar" />);
    expect(select().value).toBe("5");
    first.unmount();
    at("/");
    window.sessionStorage.setItem(TEAM_STORAGE_KEY, ":99");
    render(<TeamSwitcher teams={teams} variant="sidebar" />);
    expect(select().value).toBe("2");
  });
});

describe("the shell", () => {
  const html = (props: Record<string, unknown> = {}) => renderToStaticMarkup(<AppShell current="tasks" {...props}>x</AppShell>);

  it("with the switch off (no teams) draws nothing new: no switcher, no Teams link, no mark, plain links", () => {
    const off = html();
    expect(off).not.toContain("team-switch");
    expect(off).not.toContain(SHELL_MARK);
    expect(off).not.toContain('href="/teams"');
    expect(off).not.toContain("?team=");
    for (const href of ["/tasks", "/projects", "/team", "/settings"]) expect(off).toContain(`href="${href}"`);
  });

  it("with the switch on draws the switcher twice (sidebar and top bar), a Teams link and the mark", () => {
    const on = html({ teams });
    expect(on).toContain(SHELL_MARK);
    expect(on.match(/team-switch-select/g)).toHaveLength(2);
    expect(on).toContain("team-switch-sidebar");
    expect(on).toContain("team-switch-topbar");
    expect(on).toContain('href="/teams"');
    // The default team: the links are as before.
    expect(on).toContain('href="/tasks"');
  });

  it("the sidebar's team links carry the tab's team", () => {
    at("/tasks", "team=4");
    const { container } = render(
      <AppShell current="tasks" teams={teams}>
        x
      </AppShell>,
    );
    const hrefs = Array.from(container.querySelectorAll("aside a")).map((a) => a.getAttribute("href"));
    for (const href of ["/?team=4", "/tasks?team=4", "/projects?team=4", "/decisions?team=4", "/review?team=4", "/settings?team=4"])
      expect(hrefs).toContain(href);
    expect(hrefs).toContain("/teams"); // the Teams list is not one team's
    expect(hrefs).toContain("/ask");
  });

  it("has ONE Teams entry (no separate Team link) while the switch is on, and the plain Team link while it is off", () => {
    const { container } = render(
      <AppShell current="team" teams={teams}>
        x
      </AppShell>,
    );
    const links = Array.from(container.querySelectorAll("aside nav a")).map((a) => [a.textContent, a.getAttribute("href"), a.getAttribute("aria-current")]);
    expect(links.filter(([text]) => /^Teams?$/.test(String(text)))).toEqual([["Teams", "/teams", "page"]]); // a team's page is under Teams
    expect(links.map(([, href]) => href)).not.toContain("/team");
    cleanup();
    const off = render(<AppShell current="team">x</AppShell>);
    const offLinks = Array.from(off.container.querySelectorAll("aside nav a")).map((a) => a.getAttribute("href"));
    expect(offLinks).toContain("/team");
    expect(offLinks).not.toContain("/teams");
  });

  it("names the default team on the shell, so a call with no choice still names a team", () => {
    const { container } = render(
      <AppShell current="tasks" teams={teams}>
        x
      </AppShell>,
    );
    expect(container.querySelector(`[${SHELL_MARK}]`)?.getAttribute("data-default-team")).toBe("2");
  });
});

describe("keeping the tab's team in step", () => {
  it("remembers the team of a team page", () => {
    at("/tasks", "team=4");
    render(<ActiveTeamSync teams={teams} />);
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBe(":4");
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("a team page opened without ?team= while a team is remembered is replaced by that team's", () => {
    window.sessionStorage.setItem(TEAM_STORAGE_KEY, ":5");
    at("/projects");
    render(<ActiveTeamSync teams={teams} />);
    expect(nav.replace).toHaveBeenCalledWith("/projects?team=5");
  });

  it("the Meetings list (the home page) follows the remembered team too", () => {
    window.sessionStorage.setItem(TEAM_STORAGE_KEY, ":5");
    at("/");
    render(<ActiveTeamSync teams={teams} />);
    expect(nav.replace).toHaveBeenCalledWith("/?team=5");
  });

  it("does not touch a page that is not one team's", () => {
    window.sessionStorage.setItem(TEAM_STORAGE_KEY, ":5");
    at("/ask");
    render(<ActiveTeamSync teams={teams} />);
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("a remembered team the person is no longer in is forgotten, with a plain notice", () => {
    window.sessionStorage.setItem(TEAM_STORAGE_KEY, ":99");
    at("/tasks");
    render(<ActiveTeamSync teams={teams} />);
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBeNull();
    expect(screen.getByRole("status").textContent).toContain("That team is no longer available to you, so you are back on your default team.");
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("shows the notice the server redirected with, once, and takes it out of the address", () => {
    at("/tasks", "notice=team-gone");
    render(<ActiveTeamSync teams={teams} />);
    expect(screen.getByRole("status").textContent).toContain("no longer available to you");
    expect(window.location.search).toBe("");
  });
});

// The live bug of 2026-11-14: the switcher and the sync are both on every page, and the page that is leaving
// still has the OLD team in its address while the new page loads. jsdom cannot navigate, so `at()` keeps the old
// address, exactly what the browser shows until the load commits. These tests mount BOTH, as the shell does;
// the tests above mount one at a time and could not see it.
describe("choosing a team while the sync is mounted", () => {
  const both = (list = teams) => (
    <>
      <TeamSwitcher teams={list} variant="sidebar" />
      <ActiveTeamSync teams={list} />
    </>
  );

  it("choosing the default team from another team's page stays on the default team", () => {
    at("/tasks", "team=4");
    render(both());
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBe(":4"); // the sync remembered the address's team
    fireEvent.change(select(), { target: { value: "2" } });
    expect(load).toHaveBeenCalledWith("/tasks");
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBeNull(); // not put back by the sync
    expect(nav.replace).not.toHaveBeenCalled();
    // ... and the page that loads (no ?team=, nothing remembered) is left as it is.
    cleanup();
    at("/tasks");
    render(both());
    expect(nav.replace).not.toHaveBeenCalled();
    expect(select().value).toBe("2");
  });

  it.each(["/", "/tasks", "/decisions", "/review"])("%s: default team to another team, and back, sticks", (path) => {
    at(path);
    render(both());
    fireEvent.change(select(), { target: { value: "4" } });
    expect(load).toHaveBeenLastCalledWith(`${path}?team=4`);
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBe(":4");
    expect(nav.replace).not.toHaveBeenCalled(); // no second, soft transition racing the full load
    cleanup();
    window.dispatchEvent(new Event("pageshow"));
    at(path, "team=4"); // the page that loaded
    render(both());
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBe(":4");
    fireEvent.change(select(), { target: { value: "2" } });
    expect(load).toHaveBeenLastCalledWith(path);
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBeNull();
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("from one non-default team to another, the new choice is what stays remembered", () => {
    at("/decisions", "team=4");
    render(both());
    fireEvent.change(select(), { target: { value: "5" } });
    expect(load).toHaveBeenCalledWith("/decisions?team=5");
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBe(":5");
  });

  it("a server refresh while the new page is loading (new `teams` array) does not undo the choice", () => {
    at("/review", "team=5");
    const view = render(both());
    fireEvent.change(select(), { target: { value: "2" } });
    view.rerender(both([...teams]));
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBeNull();
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it("the shell's one-ordinary-team shape (the ordinary team is the default): choosing the personal space, then the default again, sticks", () => {
    const shape = teams.map((t) => ({ ...t, is_default: t.id === 4 })); // personal (2) + Platform team (4, the default) + Client Y (5)
    at("/");
    render(both(shape));
    expect(select().value).toBe("4");
    fireEvent.change(select(), { target: { value: "2" } });
    expect(load).toHaveBeenLastCalledWith("/?team=2");
    cleanup();
    window.dispatchEvent(new Event("pageshow"));
    at("/", "team=2");
    render(both(shape));
    fireEvent.change(select(), { target: { value: "4" } });
    expect(load).toHaveBeenLastCalledWith("/");
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBeNull();
  });
});
