// The team a browser TAB is working on (multi-team stage 3). Nothing is kept on the server: each
// team-scoped request names its team in the `X-Team-Id` header (lib/api/client.ts), so two tabs can
// work on two teams. The team lives in two places, both per tab:
//   1. the URL, `?team=<id>`, on the team-scoped pages (Team, Tasks, Projects, Settings). The server
//      render reads this one: it cannot see the browser's storage.
//   2. `sessionStorage`, which remembers the choice for the pages that carry no `?team=` (the meeting
//      page, Ask, Notifications). sessionStorage belongs to one tab, so a second tab never sees it.
// Switching team is a FULL page load (components/app/team-switcher.tsx), so every screen reads again
// for the team it opens on.
// None of this exists while the server's `multi_team` switch is off: the shell draws no switcher and
// marks nothing, and `activeTeamForRequest()` answers null.

export const TEAM_PARAM = "team";
export const NOTICE_PARAM = "notice";
export const TEAM_GONE_NOTICE = "team-gone";
export const TEAM_STORAGE_KEY = "beyric.active-team";
// The shell carries this attribute while the switch is on; the interceptor looks for it so that a
// stale `?team=` in an old bookmark does nothing while the switch is off.
export const SHELL_MARK = "data-multi-team";
// The request header proxy.ts sets from a page's `?team=`, for the server render's first call (lib/api/server.ts).
export const ACTIVE_TEAM_HEADER = "x-active-team";
// ... and the id of the person's default team, so a call that names no choice still names a team (the
// lists, the counts and the board then always agree on one team: lib/api/server.ts `resolveTeam`).
export const SHELL_DEFAULT_TEAM = "data-default-team";

// Pages whose content is one team's: they carry `?team=` in the URL. The Meetings list (the home page
// `/`), Decisions and Review follow the team too; the meeting page, Ask and Notifications do not (a
// meeting opens by its link whatever team is active).
export const TEAM_PAGES = ["/", "/team", "/tasks", "/projects", "/decisions", "/review", "/settings"] as const;

// The largest id the API accepts in `X-Team-Id` (`MAX_ID` in app/services.py): beyond it the server
// answers 422, so such a value (a hand-edited URL or storage) counts as no team at all.
export const MAX_TEAM_ID = 2_147_483_647;

// A team id as the API issues them: a whole number from 1 to MAX_TEAM_ID.
export function parseTeamId(raw: unknown): number | null {
  const text = Array.isArray(raw) ? raw[0] : raw;
  const id = typeof text === "number" ? text : typeof text === "string" && /^\d{1,10}$/.test(text) ? Number(text) : NaN;
  return Number.isSafeInteger(id) && id > 0 && id <= MAX_TEAM_ID ? id : null;
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

// Who is signed in, as the shell marks it (a short hash of the address, never the address): the remembered
// team belongs to that person. Another person signing in in this tab (logout and login, a second account)
// finds no remembered team, so A's choice is never sent as B's. "" when no shell is on the page.
function personKey(): string {
  if (typeof document === "undefined") return "";
  const mark = document.querySelector(`[${SHELL_MARK}]`)?.getAttribute(SHELL_MARK) ?? "";
  let hash = 5381;
  for (const ch of mark) hash = ((hash * 33) ^ ch.charCodeAt(0)) >>> 0;
  return mark ? hash.toString(36) : "";
}

// Stored as "<person>:<team id>".
export function readStoredTeam(): number | null {
  try {
    const [who, id] = (storage()?.getItem(TEAM_STORAGE_KEY) ?? "").split(":");
    return who === personKey() ? parseTeamId(id) : null;
  } catch {
    return null;
  }
}

export const TEAM_EVENT = "beyric:active-team";

export function writeStoredTeam(teamId: number | null): void {
  try {
    if (teamId === null) storage()?.removeItem(TEAM_STORAGE_KEY);
    else storage()?.setItem(TEAM_STORAGE_KEY, `${personKey()}:${teamId}`);
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

// The default team the shell names (null while the switch is off or no shell is on the page).
export function shellDefaultTeam(): number | null {
  if (typeof document === "undefined") return null;
  return parseTeamId(document.querySelector(`[${SHELL_MARK}]`)?.getAttribute(SHELL_DEFAULT_TEAM));
}

// What the browser's API client sends as `X-Team-Id`: the tab's team, else the person's default team, so
// the screen, its counts and the sidebar badges are always about ONE team (null while the switch is off).
export function activeTeamForRequest(): number | null {
  if (!multiTeamOn()) return null;
  return activeTeam() ?? shellDefaultTeam();
}

// The team the API told us is not ours (404 "No such team."): forget it and go to the same page on
// the default team, with a notice. A full load, so the server renders it fresh.
//
// Never twice in a row: a reset that came less than RESET_GAP_MS after the last one forgets the team but does
// not reload, so a server that keeps answering "No such team." cannot trap the tab in a reload loop.
export const RESET_KEY = "beyric.team-reset-at";
export const RESET_GAP_MS = 15_000;
export function resetToDefaultTeam(): void {
  if (typeof window === "undefined") return;
  writeStoredTeam(null);
  try {
    const last = Number(storage()?.getItem(RESET_KEY) ?? 0);
    if (Date.now() - last < RESET_GAP_MS) return;
    storage()?.setItem(RESET_KEY, String(Date.now()));
  } catch {
    // No storage: the reload below still happens once per call.
  }
  const url = new URL(window.location.href);
  url.searchParams.delete(TEAM_PARAM);
  url.searchParams.set(NOTICE_PARAM, TEAM_GONE_NOTICE);
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.href = `${url.pathname}${url.search}${url.hash}`;
}

export const TEAM_GONE_MESSAGE = "That team is no longer available to you, so you are back on your default team.";
