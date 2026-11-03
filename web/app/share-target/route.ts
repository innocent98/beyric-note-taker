import { findMeetingLink } from "@/lib/share";

// The address in the manifest's `share_target` (app/manifest.ts). Android's share sheet makes the
// browser POST the shared title, text and url here as a form. The body (which can hold a whole
// invitation, and a Zoom passcode) is read here and goes no further: this handler keeps no copy,
// logs nothing and calls no API. It finds the first Meet or Zoom link and sends the browser on to
// /share with only that link after the `#`, which is never sent to a server, so no access log sees
// it. The page then asks the owner to confirm before anything is sent to the note taker.
//
// No Origin check, and no CSRF token to ask for: a share comes from the system sheet, not from one
// of our pages, so the browser has no origin to prove. That is safe because nothing happens here
// that a forged POST could use: it changes no data and starts no bot. The only thing it can do is
// open /share with a link already filled in, and /share still needs the owner's own tap, which goes
// through the API's CSRF rules like every other write.
export async function POST(request: Request) {
  const values: { title?: string; text?: string; url?: string } = {};
  try {
    const form = await request.formData();
    for (const key of ["title", "text", "url"] as const) {
      const value = form.get(key);
      if (typeof value === "string") values[key] = value;
    }
  } catch {
    // Not a form (or too big to read): treated as a share with nothing in it.
  }
  const link = findMeetingLink(values);
  const fragment = link
    ? `link=${encodeURIComponent(link.url)}&others=${link.others}`
    : "none";
  return new Response(null, {
    status: 303,
    headers: { Location: `/share#${fragment}`, "Cache-Control": "no-store" },
  });
}

// A link or a prefetch to this address does nothing.
export function GET() {
  return new Response(null, { status: 303, headers: { Location: "/share#none", "Cache-Control": "no-store" } });
}
