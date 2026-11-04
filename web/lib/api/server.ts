import { cache } from "react";
import { headers } from "next/headers";
import { notFound, redirect } from "next/navigation";
import { ApiError, createApi } from "./client";
import type { Pilot, SessionState } from "./types";

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
export async function serverApi(extra: { onResponse?: (response: Response) => void } = {}) {
  const incoming = await headers();
  return createApi({
    ...extra,
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

// Who is looking, once per request. null when FastAPI cannot be reached: pages that only need to
// know "owner or not" (the public header, the landing) then treat the visitor as logged out.
export const getSession = cache(async (): Promise<SessionState | null> => {
  try {
    return await (await serverApi()).session();
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

// The unread count of the session this request already read (`unread_notifications`): the number
// beside Notifications in the sidebar. null when it is not known; the shell then draws no badge.
export async function getUnreadNotifications(): Promise<number | null> {
  return (await getSession())?.unread_notifications ?? null;
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
