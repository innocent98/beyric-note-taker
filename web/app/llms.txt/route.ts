import { SITE_DESCRIPTION, SITE_NAME, SITE_URL, LEGAL_NAME } from "@/lib/site";

// A plain-text summary for language-model crawlers (llmstxt.org). Same rule as the JSON-LD:
// only what is true of the pilot today (docs/sop/2026-11-14-landing-security-copy.md, docs/sop/2026-11-14-landing-v2-and-pricing.md).
export const dynamic = "force-static";

export function GET() {
  const body = `# ${SITE_NAME}

> ${SITE_DESCRIPTION}

${SITE_NAME} is run by ${LEGAL_NAME}, Lagos, Nigeria. It is in a pilot with a limited number of accounts, and sign-up is invite-only for now. Each account has an hours limit. The sign-up page says whether places are open; when the pilot is full or closed there is a waiting list.

## What it does

- Joins a Google Meet, Microsoft Teams or Zoom call from a link and records the audio.
- Produces a transcript and notes: a summary, decisions and action items.
- Notes follow a template you choose, and a vocabulary list keeps names and terms spelled correctly.
- Connect Google Calendar so the note taker joins the meetings your rules choose by itself. Outlook calendar support is new and still being tested.
- Tasks go onto a board by project and sprint. A team's owner or admin can connect Slack, which gets a message after a shared meeting's notes are written, and Linear, which gets an issue for each task.
- Decisions are collected in a searchable log, and recurring meetings carry unfinished tasks and open questions forward.

## Pages

- [Pricing](${SITE_URL}/pricing): the plans we expect to offer. No plan is on sale yet; every price and number of hours is still to be announced.
- [Privacy Policy](${SITE_URL}/privacy): what is collected, how Google user data is handled, retention, your rights.
- [Terms of Service](${SITE_URL}/terms): rules for use, including your duty to tell participants about recording.

## Contact

privacy@beyrictech.com
`;
  return new Response(body, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
}
