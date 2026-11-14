// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  activeTeam,
  isTeamPage,
  NOTICE_PARAM,
  parseTeamId,
  readStoredTeam,
  resetToDefaultTeam,
  SHELL_MARK,
  TEAM_STORAGE_KEY,
  withTeam,
  writeStoredTeam,
} from "@/lib/active-team";

function at(path: string) {
  window.history.replaceState(null, "", path);
}
function markShell(on: boolean) {
  document.body.innerHTML = on ? `<div ${SHELL_MARK}></div>` : "";
}

beforeEach(() => {
  window.sessionStorage.clear();
  markShell(true);
  at("/");
});
afterEach(() => {
  document.body.innerHTML = "";
});

describe("parseTeamId", () => {
  it.each([
    ["5", 5],
    [["7", "8"], 7],
    [12, 12],
  ])("%j is %j", (raw, id) => expect(parseTeamId(raw)).toBe(id));
  it.each(["", "0", "-1", "1.5", "abc", "5; drop", "12345678901", null, undefined, 0, 2.5])("%j is not an id", (raw) =>
    expect(parseTeamId(raw)).toBeNull(),
  );
});

describe("links", () => {
  it("only the team screens are team pages", () => {
    for (const p of ["/team", "/tasks", "/projects", "/settings", "/tasks/", "/tasks?x=1"]) expect(isTeamPage(p)).toBe(true);
    for (const p of ["/", "/teams", "/meetings/3", "/ask", "/settings/x"]) expect(isTeamPage(p)).toBe(false);
  });
  it("withTeam sets, replaces and removes ?team= and keeps the rest", () => {
    expect(withTeam("/tasks", 5)).toBe("/tasks?team=5");
    expect(withTeam("/tasks?team=3", 5)).toBe("/tasks?team=5");
    expect(withTeam("/settings?calendar=connected", 5)).toBe("/settings?calendar=connected&team=5");
    expect(withTeam("/tasks?team=3", null)).toBe("/tasks");
    expect(withTeam("/team#members", 2)).toBe("/team?team=2#members");
  });
  it("leaves a link to any other page alone", () => {
    expect(withTeam("/ask", 5)).toBe("/ask");
    expect(withTeam("/meetings/3?tab=transcript", 5)).toBe("/meetings/3?tab=transcript");
  });
});

describe("the tab's team", () => {
  it("is the URL's on a team page, else the remembered one, else none", () => {
    expect(activeTeam()).toBeNull();
    writeStoredTeam(4);
    expect(activeTeam()).toBe(4);
    at("/tasks?team=9");
    expect(activeTeam()).toBe(9);
    at("/meetings/3?team=9"); // not a team page: the parameter means nothing there
    expect(activeTeam()).toBe(4);
  });
  it("is nothing at all while the switch is off, whatever is stored or typed", () => {
    markShell(false);
    writeStoredTeam(4);
    at("/tasks?team=9");
    expect(activeTeam()).toBeNull();
  });
  it("is per tab: it lives in sessionStorage, never localStorage", () => {
    writeStoredTeam(4);
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBe("4");
    expect(window.localStorage.getItem(TEAM_STORAGE_KEY)).toBeNull();
  });
  it("forgets a team, and ignores a stored value that is not an id", () => {
    writeStoredTeam(4);
    writeStoredTeam(null);
    expect(readStoredTeam()).toBeNull();
    window.sessionStorage.setItem(TEAM_STORAGE_KEY, "abc");
    expect(readStoredTeam()).toBeNull();
  });
});

describe("resetToDefaultTeam", () => {
  it("forgets the team and goes to the same page without it, with the notice", () => {
    let target = "";
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, href: "http://localhost/tasks?team=9&x=1", pathname: "/tasks", search: "?team=9&x=1", hash: "" },
    });
    Object.defineProperty(window.location, "href", { configurable: true, get: () => "http://localhost/tasks?team=9&x=1", set: (v: string) => (target = v) });
    writeStoredTeam(9);
    resetToDefaultTeam();
    expect(readStoredTeam()).toBeNull();
    expect(target).toBe(`/tasks?x=1&${NOTICE_PARAM}=team-gone`);
  });
});
