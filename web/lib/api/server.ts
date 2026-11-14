import { cache } from "react";
import { headers } from "next/headers";
import { notFound, redirect } from "next/navigation";
import { ApiError, createApi } from "./client";
import { ACTIVE_TEAM_HEADER, parseTeamId, NOTICE_PARAM, TEAM_GONE_NOTICE, TEAM_PARAM } from "@/lib/active-team";
import type { Pilot, SessionState, TeamList } from "./types";

// FastAPI's address inside the compose network (the same variable next.config.ts reads for the
// fallback proxy, set at runtime in compose.yaml).
export function fastapiUrl(): string {
  return (process.env.FASTAPI_INTERNAL_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
}

// Headers a server-side call passes on to FastAPI. `x-forwarded-for` is the visitor's address (the
// login limits key on it) and `x-edge-secret` is nginx's proof that the address is genuine:
// FastAPI believes the address only when the secret is right. Next never reads the secret and
// never invents one, so a request that did not come through nginx has nothing to forward.
const FORWARDED = ["cookie", "x-forwarded-for", "x-forwarded-proto", "x-edge-secret"];

// The API as a server component or route handler calls it: the visitor's Cookie (and address, for
// FastAPI's own limits and logs) are passed on. A 401 leaves the page for /login.
// `teamId` is the team this page is about, sent as `X-Team-Id` on the team-scoped calls (`resolveTeam` makes
// it from `?team=`, or the default team); null or left out: no header, the API's default team.
export async function serverApi(
  extra: { onResponse?: (response: Response) => void; teamId?: number | null } = {},
) {
  const incoming = await headers();
  const { teamId, ...rest } = extra;
  return createApi({
    ...rest,
    ...(teamId ? { teamId: () => teamId } : {}),
    baseUrl: fastapiUrl(),
    headers: () => {
      const out: Record<string, string> = {};
      for (const name of FORWARDED) {
        const value = incoming.get(name);
        if (value) out[name] = value;
      }
      return out;
    },
  });
}

// The team the page's own `?team=` names, handed on by proxy.ts as `x-active-team` (the first answer of
// a page, the session with its sidebar badges, is read before the page has parsed its own parameters).
// null when the address names none (the person's default team applies) or the value is not an id.
async function addressTeam(): Promise<number | null> {
  return parseTeamId((await headers()).get(ACTIVE_TEAM_HEADER));
}

// Who is looking, once per request. null when FastAPI cannot be reached: pages that only need to
// know "owner or not" (the public header, the landing) then treat the visitor as logged out. The
// `badges` count the team of the address (`?team=`), else the default team; a team the person is not
// in is not an error here, it counts the default team (the page itself answers a stale `?team=`).
export const getSession = cache(async (): Promise<SessionState | null> => {
  try {
    return await (await serverApi({ teamId: await addressTeam() })).session();
  } catch {
    return null;
  }
});

// The pilot's state for the sign-up and sign-in pages (public). The visitor's cookie is passed on,
// because `prefill_email` rides in a cookie. null when FastAPI cannot be reached; the pages then
// draw the plain form and let the API's own answer decide.
export const getPilot = cache(async (): Promise<Pilot | null> => {
  try {
    return await (await serverApi()).pilot();
  } catch {
    return null;
  }
});

// The count beside Review in the sidebar: `badges.review_open` of the session this request already
// read. null when it is not known (FastAPI unreachable); the shell then draws no badge.
export async function getReviewOpen(): Promise<number | null> {
  return (await getSession())?.badges?.review_open ?? null;
}

// The count beside Tasks in the sidebar: `badges.tasks_new`. null when it is not known.
export async function getTasksNew(): Promise<number | null> {
  return (await getSession())?.badges?.tasks_new ?? null;
}

// The unread count of the session this request already read (`unread_notifications`): the number
// beside Notifications in the sidebar. null when it is not known; the shell then draws no badge.
export async function getUnreadNotifications(): Promise<number | null> {
  return (await getSession())?.unread_notifications ?? null;
}

// The time zone the person chose on Settings, from the session this request already read; null when
// none was chosen (or it is not known): the pages then use the browser's zone.
export async function getTimeZone(): Promise<string | null> {
  return (await getSession())?.user?.time_zone ?? null;
}

// Who the sidebar names: the person's name and email from the session this request already read.
// null when it is not known; the shell then draws no name.
export async function getShellUser(): Promise<{ name: string | null; email: string } | null> {
  const user = (await getSession())?.user;
  return user ? { name: user.name, email: user.email } : null;
}

// Does this server let a person belong to several teams (`multi_team` of the session)? false when it
// is not known: the app then looks exactly as it did before multi-team.
export async function getMultiTeam(): Promise<boolean> {
  return (await getSession())?.multi_team === true;
}

// The person's teams, for the switcher: only while the switch is on, and null when it is off or the
// list cannot be read (the shell then draws no switcher; nothing else depends on it).
export const getTeams = cache(async (): Promise<TeamList | null> => {
  if (!(await getMultiTeam())) return null;
  try {
    return await (await serverApi()).teams.list();
  } catch {
    return null;
  }
});

// What the shell takes as `teams`: the list, or null while the switch is off (no switcher).
export async function getShellTeams() {
  return (await getTeams())?.teams ?? null;
}

type SearchParams = Record<string, string | string[] | undefined>;

// The team a page is about, and the API as that team's page calls it. `?team=<id>` names it; none (or the
// switch off) means the default team. A team the person is not in (they left, it was deleted, a
// hand-typed id) is not an error page: the person goes to the same page on the default team with a plain
// notice. `teams` is the list for the shell. While the switch is on every team-scoped call names a team,
// the default one included (`X-Team-Id`), so the list, its counts and the sidebar badges are always about
// one team. `teamId` is only what the address named (null: the default).
export async function resolveTeam(path: string, searchParams: SearchParams) {
  const teams = await getTeams();
  let teamId: number | null = null;
  const raw = searchParams[TEAM_PARAM];
  if (teams && raw !== undefined) {
    const asked = parseTeamId(raw);
    if (asked !== null && teams.teams.some((t) => t.id === asked)) teamId = asked;
    else redirect(`${path}?${NOTICE_PARAM}=${TEAM_GONE_NOTICE}`);
  }
  const named = teams ? (teamId ?? teams.teams.find((t) => t.is_default)?.id ?? null) : null;
  const api = await serverApi({ teamId: named });
  return { api, teams, teamId };
}

// For the team pages (Home, Team, Tasks, Projects, Decisions, Review, Settings): like `requireOwner`,
// plus the team the page is about (`resolveTeam`).
export async function requireTeamOwner(path: string, searchParams: SearchParams) {
  const session = await getSession();
  if (!session?.authenticated) redirect("/login");
  return resolveTeam(path, searchParams);
}

// For pages that are only for the owner. Anyone else goes to /login.
export async function requireOwner() {
  const session = await getSession();
  if (!session?.authenticated) redirect("/login");
  return serverApi();
}

// Wraps a server-side API call: a 401 (cookie expired or revoked since the check) goes to /login
// and a 404 to the not-found page; any other failure reaches app/error.tsx.
export async function load<T>(call: Promise<T>): Promise<T> {
  try {
    return await call;
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }
}
