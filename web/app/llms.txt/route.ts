import { SITE_DESCRIPTION, SITE_NAME, SITE_URL, LEGAL_NAME } from "@/lib/site";

// A plain-text summary for language-model crawlers (llmstxt.org). Same rule as the JSON-LD:
// only what is true of the pilot today.
export const dynamic = "force-static";

export function GET() {
  const body = `# ${SITE_NAME}

> ${SITE_DESCRIPTION}

${SITE_NAME} is run by ${LEGAL_NAME}, Lagos, Nigeria. It is in a private pilot with a small group of testers; there is no public sign-up yet.

## What it does

- Joins a Google Meet or Zoom call from a link and records the audio.
- Produces a transcript and notes: a summary, decisions and action items.
- Notes follow a template you choose, and a vocabulary list keeps names and terms spelled correctly.
- Coming soon: connect Google Calendar so the note taker joins chosen meetings by itself.

## Pages

- [Privacy Policy](${SITE_URL}/privacy): what is collected, how Google user data is handled, retention, your rights.
- [Terms of Service](${SITE_URL}/terms): rules for use, including your duty to tell participants about recording.

## Contact

privacy@beyrictech.com
`;
  return new Response(body, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
}
