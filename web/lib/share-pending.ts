import { findMeetingLink, type SharedLink } from "@/lib/share";

// A shared link waiting for a login, kept in this tab's sessionStorage (never in a cookie, a URL or
// a log): it survives the trip to /login and back, goes when the tab closes, and is dropped after
// PENDING_MINUTES. Whatever is read back is checked again with findMeetingLink, so a value someone
// planted in storage is no more trusted than shared text. Storage can be blocked or full: every
// call then does nothing, and the page asks for the link to be shared again.
const KEY = "beyric.pending-share";
export const PENDING_MINUTES = 15;

type Stored = { url: string; others: number; at: number };

export function stashShare(link: SharedLink): void {
  try {
    const value: Stored = { url: link.url, others: link.others, at: Date.now() };
    window.sessionStorage.setItem(KEY, JSON.stringify(value));
  } catch {
    // Storage is unavailable: nothing to keep.
  }
}

export function peekShare(now = Date.now()): SharedLink | null {
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<Stored> | null;
    if (typeof value?.url !== "string" || typeof value.at !== "number") return clearShare();
    if (now - value.at > PENDING_MINUTES * 60_000) return clearShare();
    const link = findMeetingLink({ url: value.url });
    if (!link) return clearShare();
    return { ...link, others: typeof value.others === "number" && value.others > 0 ? Math.min(value.others, 99) : 0 };
  } catch {
    return null;
  }
}

export function hasPendingShare(): boolean {
  return peekShare() !== null;
}

export function clearShare(): null {
  try {
    window.sessionStorage.removeItem(KEY);
  } catch {
    // Nothing to clear.
  }
  return null;
}

// What app/share-target/route.ts puts after the # of /share: `link=<encoded link>&others=<n>`, or
// `none` when it found no meeting link. A fragment is not sent to any server, so it reaches no log.
export function readShareFragment(hash: string): { link: SharedLink | null; seen: boolean } {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  const url = params.get("link");
  if (url) {
    const link = findMeetingLink({ url });
    const others = Number(params.get("others"));
    return { link: link ? { ...link, others: Number.isInteger(others) && others > 0 ? Math.min(others, 99) : 0 } : null, seen: true };
  }
  return { link: null, seen: params.has("none") };
}
