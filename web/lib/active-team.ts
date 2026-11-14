// The team a browser TAB is working on (multi-team stage 3). Nothing is kept on the server: each
// team-scoped request names its team in the `X-Team-Id` header (lib/api/client.ts), so two tabs can
// work on two teams. The team lives in two places, both per tab:
//   1. the URL, `?team=<id>`, on the team-scoped pages (Team, Tasks, Projects, Settings). The server
//      render reads this one: it cannot see the browser's storage.
//   2. `sessionStorage`, which remembers the choice for the pages that carry no `?team=` (the meeting
//      page, the Meetings list). sessionStorage belongs to one tab, so a second tab never sees it.
// None of this exists while the server's `multi_team` switch is off: the shell draws no switcher and
// marks nothing, and `activeTeamForRequest()` answers null.

export const TEAM_PARAM = "team";
export const NOTICE_PARAM = "notice";
export const TEAM_GONE_NOTICE = "team-gone";
export const TEAM_STORAGE_KEY = "beyric.active-team";
// The shell carries this attribute while the switch is on; the interceptor looks for it so that a
// stale `?team=` in an old bookmark does nothing while the switch is off.
export const SHELL_MARK = "data-multi-team";

// Pages whose content is one team's: they carry `?team=` in the URL.
export const TEAM_PAGES = ["/team", "/tasks", "/projects", "/settings"] as const;

// A team id as the API issues them: a positive whole number.
export function parseTeamId(raw: unknown): number | null {
  const text = Array.isArray(raw) ? raw[0] : raw;
  if (typeof text === "number") return Number.isSafeInteger(text) && text > 0 ? text : null;
  if (typeof text !== "string" || !/^\d{1,10}$/.test(text)) return null;
  const id = Number(text);
  return id > 0 ? id : null;
}

export function isTeamPage(path: string): boolean {
  const clean = path.split(/[?#]/, 1)[0].replace(/\/$/, "") || "/";
  return (TEAM_PAGES as readonly string[]).includes(clean);
}

// `href` with `?team=<id>` set (replacing an earlier one) when `teamId` is given, without it when null.
// Only a team page carries it; any other href is returned as it was.
export function withTeam(href: string, teamId: number | null): string {
  const [rest, hash = ""] = href.split("#", 2);
  const [path, query = ""] = rest.split("?", 2);
  if (!isTeamPage(path)) return href;
  const params = new URLSearchParams(query);
  params.delete(TEAM_PARAM);
  if (teamId !== null) params.set(TEAM_PARAM, String(teamId));
  const search = params.toString();
  return `${path}${search ? `?${search}` : ""}${hash ? `#${hash}` : ""}`;
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

export function readStoredTeam(): number | null {
  try {
    return parseTeamId(storage()?.getItem(TEAM_STORAGE_KEY));
  } catch {
    return null;
  }
}

export const TEAM_EVENT = "beyric:active-team";

export function writeStoredTeam(teamId: number | null): void {
  try {
    if (teamId === null) storage()?.removeItem(TEAM_STORAGE_KEY);
    else storage()?.setItem(TEAM_STORAGE_KEY, String(teamId));
  } catch {
    // Private mode: the URL still carries the team on the team pages.
  }
  if (typeof window !== "undefined") window.dispatchEvent(new Event(TEAM_EVENT));
}

function urlTeam(): number | null {
  if (typeof window === "undefined") return null;
  return parseTeamId(new URLSearchParams(window.location.search).get(TEAM_PARAM));
}

// Is the switch on? The server renders the mark on the shell only then.
export function multiTeamOn(): boolean {
  return typeof document !== "undefined" && document.querySelector(`[${SHELL_MARK}]`) !== null;
}

// The team of this tab: the URL's on a team page, else the remembered one. null = no choice, which
// the API reads as "your default team".
export function activeTeam(): number | null {
  if (!multiTeamOn()) return null;
  return (isTeamPage(window.location.pathname) ? urlTeam() : null) ?? readStoredTeam();
}

// What the browser's API client sends as `X-Team-Id` (null: send none).
export function activeTeamForRequest(): number | null {
  return activeTeam();
}

// The team the API told us is not ours (404 "No such team."): forget it and go to the same page on
// the default team, with a notice. A full load, so the server renders it fresh.
export function resetToDefaultTeam(): void {
  if (typeof window === "undefined") return;
  writeStoredTeam(null);
  const url = new URL(window.location.href);
  url.searchParams.delete(TEAM_PARAM);
  url.searchParams.set(NOTICE_PARAM, TEAM_GONE_NOTICE);
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.href = `${url.pathname}${url.search}${url.hash}`;
}

export const TEAM_GONE_MESSAGE = "That team is no longer available to you, so you are back on your default team.";
