// Finds the meeting link in what another app shared with this one (the PWA share target,
// app/share-target/route.ts). Sharing apps rarely hand over a bare link: Google Meet and Zoom both
// put it in a sentence or a block of invitation text, and on Android it arrives in `text`, not
// `url`. The rules below mirror `_parse` in app/vexa.py (host, path shape, no user info, no port,
// Zoom passcode kept, everything else in the query dropped), so a link found here is one the server
// will accept. The server still decides: this only has to find the right link and refuse the rest.
// Shared text is hostile input: it is only ever read as text, each part is cut to MAX_PART
// characters before any pattern runs, and what comes out is rebuilt from parsed parts.

export type SharedLink = {
  url: string;
  platform: "google_meet" | "zoom";
  // How many other, different meeting links the shared text held (the first one is used).
  others: number;
};

export type SharedParts = { title?: string | null; text?: string | null; url?: string | null };

export const MAX_PART = 4000;

const ZOOM_PATH = /^\/(?:j|wc\/join)\/(\d+)\/?$/;
const MEET_PATH = /^\/([a-z]{3}-[a-z]{4}-[a-z]{3})\/?$/;
const BARE_MEET_CODE = /^[a-z]{3}-[a-z]{4}-[a-z]{3}$/;

// Either something that starts with http(s)://, or a Zoom or Meet address with no scheme that stands
// on its own (start of text, whitespace or an opening bracket before it: not the tail of another
// address, and not inside someone else's path).
const CANDIDATE =
  /https?:\/\/[^\s<>"'`]+|(?<=^|[\s<>"'(\[,;])(?:[a-z0-9-]+\.)*(?:zoom\.us|meet\.google\.com)\/[^\s<>"'`]+/gi;
const TRAILING_PUNCTUATION = /[.,;:!?)\]}]+$/;

function canonical(candidate: string): { url: string; platform: SharedLink["platform"] } | null {
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
  return null;
}

function linksIn(raw: unknown): { url: string; platform: SharedLink["platform"] }[] {
  if (typeof raw !== "string") return [];
  const text = raw.slice(0, MAX_PART);
  const bare = text.trim();
  if (BARE_MEET_CODE.test(bare)) return [{ url: `https://meet.google.com/${bare}`, platform: "google_meet" }];
  const found: { url: string; platform: SharedLink["platform"] }[] = [];
  for (const match of text.matchAll(CANDIDATE)) {
    const link = canonical(match[0]);
    if (link) found.push(link);
  }
  return found;
}

// The first Google Meet or Zoom link in the shared url, text and title (in that order), or null.
export function findMeetingLink(parts: SharedParts): SharedLink | null {
  const seen = new Map<string, { url: string; platform: SharedLink["platform"] }>();
  for (const part of [parts.url, parts.text, parts.title]) {
    for (const link of linksIn(part)) if (!seen.has(link.url)) seen.set(link.url, link);
  }
  const [first, ...rest] = [...seen.values()];
  return first ? { ...first, others: rest.length } : null;
}

// How the page names the meeting. A Zoom passcode is never shown.
export function describeLink(link: SharedLink): string {
  const parsed = new URL(link.url);
  if (link.platform === "google_meet") return `Google Meet ${parsed.pathname.slice(1).replace(/\/$/, "")}`;
  const id = /(\d+)\/?$/.exec(parsed.pathname)?.[1] ?? "";
  return `Zoom meeting ${id}${parsed.searchParams.has("pwd") ? " (with passcode)" : ""}`;
}
