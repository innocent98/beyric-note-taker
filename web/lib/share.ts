// Finds the meeting link in what another app shared with this one (the PWA share target,
// app/share-target/route.ts). Sharing apps rarely hand over a bare link: Google Meet, Microsoft Teams
// and Zoom all put it in a sentence or a block of invitation text, and on Android it arrives in
// `text`, not `url`. The rules below mirror `_parse` and `_teams` in app/meeting_url.py (host, path
// shape, no user info, no port, Zoom passcode kept, everything else in a Zoom or Meet query dropped,
// a Teams link kept whole and checked against the same characters, thread shape and size, Outlook's
// Safe Links wrapper read one level deep), so a link found here is one the server will accept. The
// server still decides: this only has to find the right link and refuse the rest.
// Shared text is hostile input: it is only ever read as text, each part is cut to MAX_PART
// characters before any pattern runs, and what comes out is rebuilt from parsed parts.

export type SharedLink = {
  url: string;
  platform: "google_meet" | "zoom" | "teams";
  // How many other, different meeting links the shared text held (the first one is used).
  others: number;
};

export type SharedParts = { title?: string | null; text?: string | null; url?: string | null };

export const MAX_PART = 4000;

const ZOOM_PATH = /^\/(?:j|wc\/join)\/(\d+)\/?$/;
const MEET_PATH = /^\/([a-z]{3}-[a-z]{4}-[a-z]{3})\/?$/;
const BARE_MEET_CODE = /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/;
// Teams, as in app/meeting_url.py: `/l/meetup-join/<thread id>/<n>` (teams.microsoft.com only) and the
// short `/meet/<digits>` (teams.microsoft.com and teams.live.com).
const TEAMS_JOIN_PATH = /^\/l\/meetup-join\/([A-Za-z0-9%._:@~-]+)(?:\/[0-9]{1,4})?\/?$/;
const TEAMS_THREAD = /^19:[A-Za-z0-9_=-]+@thread\.[A-Za-z0-9]+$/;
const TEAMS_MEET_PATH = /^\/meet\/[0-9]{6,20}\/?$/;
// A Teams query (tenant and organiser context, or the `p` passcode) is kept exactly as written, so
// only these characters may be in it.
const TEAMS_QUERY = /^[A-Za-z0-9._~%:,@+=&/;!*()$-]*$/;
const MAX_TEAMS_URL_CHARS = 1500;
const MAX_URL_CHARS = 2000; // the API's own limit on a pasted link (app/services.py)
const SAFELINKS_HOST = /^[a-z0-9-]+\.safelinks\.protection\.outlook\.com$/;

// Either something that starts with http(s)://, or a Zoom, Meet or Teams address with no scheme that
// stands on its own (start of text, whitespace or an opening bracket before it: not the tail of
// another address, and not inside someone else's path).
const CANDIDATE =
  /https?:\/\/[^\s<>"'`]+|(?<=^|[\s<>"'(\[,;])(?:[a-z0-9-]+\.)*(?:zoom\.us|meet\.google\.com|teams\.microsoft\.com|teams\.live\.com)\/[^\s<>"'`]+/gi;
const TRAILING_PUNCTUATION = /[.,;:!?)\]}]+$/;

// A Teams link on `host`, rebuilt as the server rebuilds it (https, host lower-cased, path and query
// exactly as written, fragment dropped), or null. Path and query are read from the text as written,
// not from the parsed URL, which would tidy away a `/../` the server refuses.
function teamsLink(host: string, cleaned: string): string | null {
  if (cleaned.length > MAX_TEAMS_URL_CHARS) return null;
  const rest = cleaned.replace(/^https?:\/\/[^/?#]*/i, "").split("#")[0];
  const [path, ...queryParts] = rest.split("?");
  const query = queryParts.join("?");
  if (!TEAMS_QUERY.test(query)) return null;
  const join = host === "teams.microsoft.com" ? TEAMS_JOIN_PATH.exec(path) : null;
  if (join) {
    let thread: string;
    try {
      thread = decodeURIComponent(join[1]);
    } catch {
      return null;
    }
    if (!TEAMS_THREAD.test(thread)) return null;
  } else if (!TEAMS_MEET_PATH.test(path)) {
    return null;
  }
  return `https://${host}${path}${query ? `?${query}` : ""}`;
}

type Found = { url: string; platform: SharedLink["platform"] };

function canonical(candidate: string, wrapped = false): Found | null {
  const cleaned = candidate.replace(TRAILING_PUNCTUATION, "");
  if (cleaned.includes("\\")) return null;
  const withScheme = /^https?:\/\//i.test(cleaned) ? cleaned : `https://${cleaned}`;
  let parsed: URL;
  try {
    parsed = new URL(withScheme);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
  if (parsed.username || parsed.password || parsed.port) return null;
  const host = parsed.hostname;
  if (host === "meet.google.com") {
    if (!MEET_PATH.test(parsed.pathname)) return null;
    return { url: `https://${host}${parsed.pathname}`, platform: "google_meet" };
  }
  if (host === "zoom.us" || host.endsWith(".zoom.us")) {
    if (!ZOOM_PATH.test(parsed.pathname)) return null;
    const passcode = parsed.searchParams.get("pwd");
    const query = passcode ? `?pwd=${encodeURIComponent(passcode)}` : "";
    return { url: `https://${host}${parsed.pathname}${query}`, platform: "zoom" };
  }
  if (host === "teams.microsoft.com" || host === "teams.live.com") {
    const url = teamsLink(host, withScheme);
    return url ? { url, platform: "teams" } : null;
  }
  // Outlook's Safe Links wrapper around a Teams link: the link inside is the one to send. One level
  // only, and only a Teams link inside (the server does the same).
  if (!wrapped && SAFELINKS_HOST.test(host) && (parsed.pathname === "" || parsed.pathname === "/")) {
    if (cleaned.length > MAX_URL_CHARS) return null;
    const inner = parsed.searchParams.get("url");
    const found = inner ? canonical(inner, true) : null;
    return found?.platform === "teams" ? found : null;
  }
  return null;
}

function linksIn(raw: unknown): Found[] {
  if (typeof raw !== "string") return [];
  const text = raw.slice(0, MAX_PART);
  const bare = text.trim();
  if (BARE_MEET_CODE.test(bare)) return [{ url: `https://meet.google.com/${bare}`, platform: "google_meet" }];
  const found: Found[] = [];
  for (const match of text.matchAll(CANDIDATE)) {
    const link = canonical(match[0]);
    if (link) found.push(link);
  }
  return found;
}

// The first Google Meet, Microsoft Teams or Zoom link in the shared url, text and title (in that order), or null.
export function findMeetingLink(parts: SharedParts): SharedLink | null {
  const seen = new Map<string, Found>();
  for (const part of [parts.url, parts.text, parts.title]) {
    for (const link of linksIn(part)) if (!seen.has(link.url)) seen.set(link.url, link);
  }
  const [first, ...rest] = [...seen.values()];
  return first ? { ...first, others: rest.length } : null;
}

// How the page names the meeting. A Zoom passcode is never shown, and neither is any part of a Teams
// link (its path and query carry the thread, the tenant and the passcode).
export function describeLink(link: SharedLink): string {
  const parsed = new URL(link.url);
  if (link.platform === "google_meet") return `Google Meet ${parsed.pathname.slice(1).replace(/\/$/, "")}`;
  if (link.platform === "teams") return "Microsoft Teams meeting";
  const id = /(\d+)\/?$/.exec(parsed.pathname)?.[1] ?? "";
  return `Zoom meeting ${id}${parsed.searchParams.has("pwd") ? " (with passcode)" : ""}`;
}
