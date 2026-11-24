# Claude Meetings vs Beyric Note Taker: gap analysis

**Written 2026-11-24.** Analysis only: nothing shipped, no code changed. Not an SOP and not yet in the master checklist (it becomes checklist scope only when Adebayo picks items).

**Sources**
- Anthropic's own pages, read in full on 2026-11-24: [announcement](https://claude.com/blog/claude-meetings) and [resource article](https://claude.com/resources/articles/claude-meetings) (same text, dated 2026-11-12), plus the Help Center article [Use Claude Meetings](https://support.claude.com/en/articles/18325154). Linked sub-pages (Whiteboards, custom roles, sheets) were NOT read.
- Beyric: code at `main` `7ca02a37`, checked by a read-only audit with `file:line` evidence (items cite the file). Runtime flags live in the server `.env`, which was not read; code defaults are stated.
- Rule used throughout: where Anthropic's pages are silent, this document says "page silent". It does not claim Claude lacks the feature.

Claude Meetings is a **week-old beta** (Team and Enterprise only, Google Meet only). Everything below is a snapshot and will move.

---

## 1. The honest headline

The gap is **not** after-the-meeting notes. On what Anthropic's pages describe (notes, summary, action items, transcript, a page per meeting, recap email, ask about past meetings, share inside the org), Beyric is at parity or ahead, and it adds a task board, decisions log, series memory and Slack/Linear push that the pages never mention.

The gap is in four other places:

1. **Live participation.** Claude is a *participant*: you say "Claude" and ask aloud or type in chat, it answers in the call chat, it builds docs and whiteboards during the call, it can send a Slack message from the call. Beyric's bot is a silent recorder with no in-call interaction at all.
2. **Trust at the door.** Claude posts a notice in the call chat on join, by default. Beyric has the same notice and it is **on in production** (server `.env` read by Adebayo 2026-11-24; he has confirmed it once live, which platform was not recorded). The code default is still off, and Meet, Zoom and Teams have not each been proven.
3. **Distribution and price.** Claude Meetings lives inside claude.ai and Claude Desktop, for orgs that already pay for Claude, and notes plus transcripts are **free** during beta. Beyric is a separate product that charges USD 18 for 20 hours.
4. **Organisation controls and openness.** Claude has org-level switches (external guests, connector use, custom roles) and a connector ecosystem around it. Beyric has team roles only, no SSO, no audit log, and **no API, webhooks or MCP server** (so it cannot even be a connector for Claude).

## 2. Side-by-side

Status: **GAP** = Claude has it, Beyric does not. **PART** = Beyric has a weaker or unproven version. **PARITY**. **AHEAD** = Beyric has it, Claude's pages do not show it. **N/S** = Anthropic's pages are silent.

### Before the meeting

| Capability | Claude Meetings | Beyric | Status |
|---|---|---|---|
| Calendar connect | Google Calendar (an org owner enables it first) | Google, and Outlook (flag-gated, "new, still being tested") | AHEAD (Outlook) |
| Add the bot to one upcoming meeting | "Add Claude" from the Meetings calendar | Per-meeting switch on each Upcoming row, plus "Send now" for missed ones (`web/components/app/calendar-upcoming.tsx:117`, `app/send_now.py`) | PARITY |
| Auto-join rule | Per user: all meetings, or all except one-on-ones; owners cannot set it for users | Per user: `all`, `organizer` or `none`, plus "also join invites I haven't answered" (`app/gcal_rules.py:64-128`). **No "except one-on-ones" rule**; the attendee count is stored but unused | PART |
| Paste a link and send now | N/S | Yes, Meet, Teams, Zoom (`app/api.py:645`) | AHEAD or N/S |
| Join a Meet you start with no calendar entry | N/S | Yes, with the Meet API scope (`app/google_oauth.py:47`) | AHEAD |
| Platforms | Google Meet only; others "coming soon" (Computerworld says Teams next; not on Anthropic's page) | Meet, Zoom, Teams (`app/meeting_url.py:61-107`) | **AHEAD, today** |
| Who can use it | Team and Enterprise only | Anyone who signs up, trial 3 h | AHEAD |
| Pre-meeting brief | N/S | Only for recurring series: Suggested agenda, "Keeps coming up", reminder mail with last summary and carried tasks (`app/topics.py`, `app/series_mail.py`) | AHEAD for series; none for one-offs |
| If the host declines | It does not retry unless someone adds it again; the user sees the decline | Waits 10 min, fails "not let in", bell plus mail (`app/pipeline.py:258`) | PARITY |

### During the meeting

| Capability | Claude Meetings | Beyric | Status |
|---|---|---|---|
| Visible participant | Yes, tile shows its live state | Yes, a guest under a configurable bot name | PARITY |
| Consent notice in the call chat | Posts a notice on join (default) | Built (`app/chat_notice.py`) and **ON in production** (`BOT_CHAT_NOTICE_ENABLED=true` in the server `.env`, read 2026-11-24). The code default is still off (`app/config.py:522`). Confirmed once live by Adebayo; per-platform proof for Meet, Zoom and Teams is not recorded | PARITY (partly proven) |
| **Ask it a question aloud or in chat** | Yes; answer goes to the call chat | No. The bot only posts one chat line; no realtime channel anywhere (`app/skribby.py:7`) | **GAP (big)** |
| **Make docs, whiteboards, slides live** | Yes, anyone on the invite can edit live; "as many as you like" | No | **GAP (big)** |
| **Use connected tools from the call** | Yes, e.g. send a Slack message, with that person's permission, asks first | No. Slack and Linear fire automatically after notes are written; Ask Beyric is read-only (`app/integrations.py:145`) | **GAP** |
| **Live transcript** | Meeting page shows live transcript and chat while the call runs | No. The owner sees a live *state* only (Joining, Waiting to be let in, Recording), polled about every 30 s | **GAP** |
| Pause and resume listening | Ask it to stop listening, or use the page; resume from the page | No pause. "End now and write the notes" and "Cancel and remove the bot" only (`app/services.py:881,921`) | **GAP** |
| Remove it, and bring it back to the same page | Yes | Can end it; no re-add to the same meeting | PART |
| Stay length | Up to 8 h, warns before leaving | About 5 h 20 min (`app/skribby.py:1153-1171`) | PART (minor) |
| Another notetaker bot in the call | Claude leaves if an unauthorised outsider (including another company's notetaker bot) joins | Leaves 300 s after "left alone" (a silence rule, not a headcount). The "leave when only bots remain" PR #215 was **closed unmerged** | PART |
| One bot per meeting | N/S | Yes, 409 `meeting_has_note_taker`; the organiser's bot is preferred (`app/placement.py:268`) | AHEAD |

### After the meeting

| Capability | Claude Meetings | Beyric | Status |
|---|---|---|---|
| Page per meeting with notes, summary, action items, transcript | Yes | Yes. Six fixed headings (Summary, Key points, Decisions, Action items, Open questions, Please check) from user-written templates (`app/notes.py:31`) | PARITY |
| Recap email to invitees | Yes, org members on the invite | Owner gets "notes ready"; assignees get "tasks assigned"; up to 10 outside recipients, no link (`app/mail_templates.py:513`) | PARITY |
| Ask about past meetings | In a regular Claude chat | Ask Beyric across all meetings you may read, cited to the transcript line, 30 questions a day; no single-meeting scope (`app/ask.py`) | PARITY (Claude's reaches the rest of Claude: docs, mail, connectors) |
| Share | Inside the org only; guests get no page or recap | Private, share with a team, or move to a team; **no public links, no guest accounts** (`app/shares.py`) | PARITY |
| Action items to a task board | N/S | Tasks with owner and confidence, projects, sprints, milestones, Linear issues, Slack post | AHEAD |
| Decisions log, recurring-meeting memory | N/S | Decisions with status and revisit dates; unfinished tasks and open questions carry into the next occurrence | AHEAD |
| Speaker naming | N/S | Named from Meet's speaking timeline, then a line-level correction (`app/speaker_correction.py`); **no in-app speaker or line editing** | AHEAD on accuracy work; GAP on user editing |
| Export | Partner transcript export "soon" (Gong, Attention, Granola, Fireflies.ai, Otter, Plaud) | PDF and Word only, en/fr/es (`app/export.py:28`). No Markdown, text or CSV | GAP |
| Search the meeting list | N/S | No keyword search of the meetings list (search exists only in Decisions) | GAP (basic) |
| Languages | N/S | Transcription language not selectable (auto). Translation of notes and transcript: English, French, Spanish only (`app/translation.py:56`) | N/S, weak spot |
| Mobile and desktop | Claude and Claude Desktop (mobile not mentioned) | PWA with an Android share target; no native app | PART |

### Admin, security, privacy

| Capability | Claude Meetings | Beyric | Status |
|---|---|---|---|
| Org switches | Off / on for roles / on for everyone; external guests internal-only or allowed; whether members' connectors work in meetings; custom roles | Team roles owner/admin/member only; no per-meeting guest control (`app/team_admin.py`) | GAP |
| SSO, SAML, SCIM, audit log | Inherits Claude's enterprise plane (the pages do not detail it) | None of these; sign-in is email and password plus Google | GAP |
| Audio | Not saved; transcript, summary and screen descriptions only | Audio kept 7 days, then deleted (`app/config.py:613`); meeting delete removes everything incl. the vendor copy | PART (Beyric stores audio for a week) |
| Training and retention | **Page silent** | Privacy page says audio and transcripts are not used for training (relying on OpenAI and Skribby terms); transcripts and notes kept until deleted | N/S |
| ZDR, HIPAA-ready, CMEK | Not available during beta to orgs on these | None offered | PARITY |
| Data residency | Page silent | Server in France; vendors in EU, US, JP; no choice offered. Encryption claims kept narrow on purpose | N/S |

### Price and openness

| Capability | Claude Meetings | Beyric | Status |
|---|---|---|---|
| Price | Notes and transcripts free on Team and Enterprise during beta; docs and whiteboards free to 2027-02-10; tool use counts against plan | Trial 3 h, then USD 18 / 20 h, USD 40 / 50 h, USD 80 / 100 h (NGN 25k / 56k / 112k) | **GAP (commercial)** |
| Public API, webhooks, MCP | Part of a connector ecosystem (Zoom for Claude, Granola, Fathom, tl;dv, Tactiq and Grain connectors are listed on claude.com) | **None** for users (`app/auth.py:324`: session cookie plus CSRF only) | **GAP (strategic)** |

## 3. Where Beyric is genuinely ahead (use these)

1. **Zoom and Teams today**, while Claude is Meet-only. Likely temporary; plan on it closing.
2. **Anyone, not only Team and Enterprise.** Individuals, small teams and Nigerian users paying in naira through Paystack.
3. **What happens to the notes afterwards:** tasks with confidence and evidence, projects, sprints, milestones, decisions log, series carry-forward, Linear and Slack push. Anthropic's pages describe none of it (silence, not proof of absence).
4. **Outlook calendar** and **join a Meet you start yourself** with no calendar entry.
5. **Per-person hours and naira pricing**, invoices and refunds.
6. **Speaker accuracy work** specific to Meet (timeline naming and line correction), once proven on real evidence (it has not run on real evidence yet).

## 4. Ranked gaps, with a recommendation

My judgement, not a decision. "Fit" is whether it suits the rules you set (no dark shipping, no rebuilds, test locally).

| # | Gap | Why it matters | Rough size | Note |
|---|---|---|---|---|
| 1 | ~~Chat consent notice off and unproven~~ **Mostly closed: on in production** | Claude does it by default; it is the visible trust signal and a legal-safety net for users who record people | Small: record which platforms were proven, prove the rest, make the code default match the server | Remaining: Meet, Zoom and Teams each shown once; the code default is still `false`, so a fresh server would start without it |
| 2 | **Public API plus an MCP server (Beyric as a connector)** | Claude now has a connector ecosystem; notetakers appear there as connectors. Without an API or MCP, Beyric cannot be part of a Claude user's workflow, and Claude cannot ask Beyric anything | Medium | The strategic reply to a big fish: be the meeting-data layer inside Claude, not only a rival to it. Read-only first (list meetings, get notes, search) |
| 3 | **Live transcript on the meeting page** | The page Claude shows during the call is live; ours shows a state | Unknown | Depends on whether Skribby offers realtime. Not checked. The code polls only |
| 4 | **In-call interaction (ask in chat, make a doc)** | The headline Claude feature | Large; depends on the vendor | Needs a realtime channel into the call. Do not start before #3 shows what Skribby allows. A cheap first step is a chat-only "@Beyric what did we decide?" using Ask Beyric |
| 5 | **Pause and resume; re-add to the same meeting** | Claude has it; users with sensitive moments need it | Small to medium | Needs Skribby pause support; confirm first |
| 6 | **Exports (Markdown, text, CSV) and a transcript editor** | Easy wins that matter to teams moving notes elsewhere | Small | Also needed for any partner integration |
| 7 | **Org controls: external-guest policy, SSO, audit log** | Blocks larger buyers; Claude inherits Enterprise controls | Large | Defer unless a team buyer asks. Your target market is teams, so keep it on the list |
| 8 | **"Except one-on-ones" auto-join rule; meeting-list search; language choice; more translation languages** | Small parity items users will notice | Small each | The attendee count is already stored |
| 9 | **Leave when only bots remain (#215, closed)** | Another notetaker in the call keeps ours waiting | Small | Decide if it was closed on purpose |
| 10 | **Price pressure** | Free beside paid, for orgs already on Claude | Business decision | Not a build item. See section 5 |

## 5. Strategy notes

- **Do not try to out-feature Claude in the call.** Live interaction needs a realtime channel Beyric does not have and Anthropic controls end to end. Compete where Anthropic is unlikely to go soon: Zoom and Teams, individuals and small teams, Nigeria and naira, and the post-meeting work layer (tasks, sprints, decisions, series).
- **Price:** while Claude's notes are free for Team and Enterprise, a team already on Claude has little reason to pay for notes alone. Beyric's value must sit in the work layer and in the platforms Claude does not join, not in the transcript. Worth one conversation in the billing go-live (S11): whether USD 18 / 20 h is positioned against Claude, Fathom, Otter, or none.
- **Window:** Anthropic said other platforms are "coming soon". Treat Zoom and Teams as an advantage with a limited lifetime, and sequence work accordingly.
- **Becoming a connector** may turn a threat into distribution: a Claude user could ask Claude about Beyric meetings.

## 6. Questions and doc drift found on the way

1. **Chat notice: answered 2026-11-24.** The checklist (line 23) said "proven live by him (his word, 2026-11-15)" while the handoff (line 217) said "not verified". Adebayo's server read shows `BOT_CHAT_NOTICE_ENABLED=true`, and he confirms he saw the notice once. The handoff line is stale and the code default is still `false`.
2. **PR #215** (leave when only bots remain) is listed as open in the checklist and handoff; the audit found the branch not in `main` and the PR closed unmerged.
3. **`docs/openapi.json` is stale**: 102 paths, and it omits every `/api/billing` route (they exist in `app/billing/*api*.py`). Anything that generates types or a public API doc from it will be wrong.
4. **Claude's pages are silent** on retention, training use, languages, participant limits, hand-raise behaviour, and any task-tool integration beyond Slack. Do not state Claude lacks these without a better source.
5. **Third-party reporting differs from Anthropic's page** on the launch date (10 or 12 November) and on Teams support; only Anthropic's page is relied on above.

## 7. Verification

| Claim | How checked |
|---|---|
| Claude Meetings facts | Read on Anthropic's own pages (announcement, resource article, Help Center) on 2026-11-24, via two separate fetches that agreed; sub-pages unread |
| Beyric capabilities | Read-only code audit with `file:line`; chat-notice default (`app/config.py:522`, `.env.example:35`) and the `all`/`organizer`/`none` join rule (`app/gcal_rules.py:111`) re-checked by hand |
| Server state, flags, live behaviour | Only the chat-notice flag and bot names, read by Adebayo on the server 2026-11-24 (his paste); everything else **not checked** |
| Connector list on claude.com | Seen as links in web search results; those pages were not opened |
