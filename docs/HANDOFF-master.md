# Beyric Note Taker: master handoff

**Written:** 2026-11-02, about 15:50 UTC, by the second lead session. Start here: "Read `docs/HANDOFF-master.md` and continue." The first lead's handoff is in git history (PR #6).

## 1. Current state

| Thing | State |
|---|---|
| Live app | https://notes.beyrictech.com, owner login only (password in `~/beyric-notes-owner-password.txt` on Adebayo's Mac) |
| `main` / server | `fbd1d94` (PRs #1–#9 and #11 merged and deployed, `/health` 200, migration `0005`). Server `haven-respite`, app at `/opt/beyric-note-taker` (memory `beyric-note-taker-server`) |
| Open PRs | **#10** `feature/public-pages`: public landing, `/privacy`, `/terms` (code; 327 tests pass locally; not yet viewed in a browser). **This handoff update** on `docs/handoff-3`. Both need Adebayo's "merge #N". No meeting is active, so #10 is safe to deploy |
| Meeting 5 | **Done:** Google Meet, 15:20 to 16:16 UTC (about 56 min), `ready` at 16:22:50 (transcribing 4 min, speakers 2 min, notes 30 s). 4 named speakers; all six note sections; useful "Please check" items. Reviewed by the second lead; Adebayo: "it went well". Gap: one person appears as both "Godstime America" and "America Godstime" |
| Updated UI | The designer's latest `docs/ui` adds a **team and tasks** layer (6.4). Committed in PR #12 with real names and content from meeting 5, by Adebayo's choice ("Commit as is") |
| Live server change not in git | Vexa DB `transcriptions.language` widened from `varchar(10)` to `varchar(32)` at about 15:36 UTC with Adebayo's OK. See `docs/sop/2026-11-02-vexa-language-column.md` (in PR #10). It must be made permanent (6.2) |
| Novex side | `innocent98/Novex-News#10` still open (Adebayo to merge) |

## 2. Decisions (with reasons)

The first lead's decisions still stand: models, Vexa in our compose, three networks, simple CD, one bot at a time for now, bigint ids, Zoom retry. They're summarised in memory `beyric-note-taker-decisions`. New today:

| Decision | Why / who |
|---|---|
| **Pilot:** open sign-up, capped at **10 accounts**; **5 recorded hours per account for the whole pilot** (no reset); both caps removable later | Adebayo wants a few testers, not just himself |
| Over the cap: the 11th sign-up sees "pilot full" plus a waitlist; at 5 h the bot stops joining and says why; a running meeting finishes; admin (Adebayo) uncapped | Claude's calls, played back, not objected to |
| **Auto-join rules:** join every calendar meeting that has a link, **and** every Meet he starts as host, whether scheduled, "instant meeting" or meet.new, even with no calendar event. Never meetings not on his calendar that he didn't start | Adebayo. He uses all three ways of starting a Meet |
| The design adds rule choices: all with a link / only ones I organise / only when I turn it on, plus a per-meeting toggle and a template for calendar meetings | Designer, in `docs/ui` |
| Zoom links on the calendar are joined too (they can be refused, and the retry is still unproven live) | Claude's call |
| **Start Google OAuth verification now**, not live with weekly reconnects | Adebayo. Google: "Authorizations by a test user will expire seven days from the time of consent" |
| Frontend stays **server-rendered** (FastAPI and Jinja, htmx/Alpine where needed); PWA possible; not Next.js | Claude's call, explained, not objected to |
| Legal entity **Beyrichq LTD, Lagos, Nigeria**, never "Beyric Technologies" | Adebayo |
| Privacy and terms must only claim what the app does (designer draft trimmed; table in the public-pages SOP) | Google compares the policy to the app; legal risk |
| **Translation:** the original stays the main version, translation is a separate view, a language for each share, a default notes language, names and vocabulary unchanged | Adebayo's notes, matched by the designs |
| Never deploy (merge) while a meeting is `bot_sent` or `processing` | The deploy restarts web and the worker |

## 3. Designs and specs

- UI redesign: `docs/ui/Beyric Note Taker redesign/` (PRs #7 and #8, plus #9 pending): app screens including the **Calendar card, Upcoming list and Settings → Calendar**, auth, landing, emails, brand (teal `#0E6B5E`, Geist), privacy and terms. These are `.dc.html` prototypes, not deployable code.
- First lead's specs: `docs/superpowers/specs/2026-11-02-beyric-note-taker-design.md`, plan `docs/superpowers/plans/2026-11-02-beyric-note-taker-mvp.md`, deploy guide `deploy/README.md`.
- **Calendar auto-join and the pilot: no written design yet.** The brainstorm is about 80% done (section 6.4).

## 4. What's covered

`docs/checklist/master-checklist.md` is the single source of truth. **PR #10 updates it**: the new "Module 2: Pilot" section, the Vexa fix, and audio retention decided. SOP index `docs/sop/`, with 2 new SOPs in PR #10. Memory is current (`beyric-note-taker-decisions`, `beyric-note-taker-server`).

## 5. Pending

**Adebayo's to-dos**
- Merge #9, #10 and this handoff PR when ready (#10 only when no meeting is active).
- Google Cloud: create the project and OAuth consent screen (name, logo, support email, authorised domain `beyrictech.com`); verify `beyrictech.com` in Search Console (DNS TXT).
- Confirm the mailboxes `privacy@`, `legal@`, `security@beyrictech.com` exist; confirm the hosting country (France per IP lookup; check the Contabo panel); decide on a DPO; ideally a lawyer reads privacy and terms.
- Still from before: backup cron line (`deploy/README.md` step 7; the privacy policy now says backups are deleted after 14 days, so the cron must exist); Cloudflare proxy on or off; mark the 5 GitGuardian findings false positive; merge Novex-News#10; rate the Zoom #3 notes; run the hour-long meeting and a French plus English call.

**Known gaps**
- Speaker hints: whisper guessed "yoruba" for 65 of 115 lines on meeting 5. Its language labels are unreliable; it's harmless while they're only used for speakers.
- Languages today: transcription auto-detects; notes language is never set; no translation. Mixed French plus English is untested.
- Zoom retry still not seen live. Backups stay on the same server. Regenerate notes can exceed Cloudflare's 100 s if proxied.

## 6. Incoming work, in order

1. ~~Follow meeting 5 to the end.~~ Done (see section 1). Remaining: merge speaker name variants (name order swapped). Original note: Check `select id,status,step,failure_reason from meetings where id=5` (db: `docker compose exec -T db psql -U postgres -d notetaker ... </dev/null`). When it's `ready`, check: speaker names in `speaker_transcript` (Vexa has 2 speakers), notes quality, duration, and Vexa memory (`docker stats`; it was 1.7/3 GB during the call). Report each step to Adebayo. Tick "Meet call with speech" only if it's ready with sensible speakers.
2. **Make the Vexa column fix permanent:** an idempotent `alter table transcriptions alter column language type varchar(32)` after `vexa` is up, in `deploy/deploy.sh` or a one-shot compose service; add it to the Vexa upgrade routine; open an upstream issue. Small PR, with an SOP update.
3. **Check PR #10 in a browser** (the run skill or a local uvicorn; both light and dark), then wait for Adebayo's merge and verify `/`, `/privacy`, `/terms` live while logged out.
4. **Finish the auto-join design (superpowers:brainstorming), then write it.** Already agreed: everything in section 2. Still to ask, one at a time:
   - Recording notice: does the bot say or post anything when it joins (the design has "Tell participants that this meeting will be recorded")? Is the consent prompt Read.ai-style?
   - Overlapping meetings: how many bots at once (Vexa is capped at 3 GB, about one Chrome; measure).
   - How early the bot joins a calendar meeting, and what happens if the meeting is moved or cancelled.
   - Is Outlook in the first version or later (the design shows both)?
   - Who gets told when auto-join fails (in-app only until email exists?).

   Technical facts already checked:
   - Google Workspace Events API can subscribe to `//cloudidentity.googleapis.com/users/USER` ("all meeting spaces where the user is the owner"), event `google.workspace.meet.conference.v2.started`, delivered by Pub/Sub.
   - **Unknown:** whether that works for personal @gmail.com accounts. Plan a throwaway spike as step one of the build.

   Then write `docs/superpowers/specs/2026-11-0X-pilot-accounts-calendar-design.md`, run the builder check (subagent), get Adebayo's approval, then superpowers:writing-plans. Build order: accounts → Google Calendar auto-join → instant-Meet auto-join → several bots at once → submit verification with a demo video.
5. **The updated UI's new features** (not in any design doc yet; scope them with Adebayo before building; they come after accounts):
   - **Team:** an admin invites teammates.
   - **Tasks from action items:** each action item becomes a task for the teammate named as its owner, matched by name; items with no match wait under "Needs owner"; optional email to assignees.
   - **Sprints:** sprint length options; "In current sprint" or "In backlog".
   - **A decisions log** across meetings, newest first.
   - **Agenda:** "Add to next agenda" and "Finished since last meeting".
   - **Sync** to Jira, Linear, Asana or Taiga.
   - **Languages** (French, Spanish and others).
   - `Beyric Note Taker v1.dc.html` is the previous version, kept by the designer.
6. Later backlog (checklist): email notes and sharing, Teams links, languages and translation, Zoom RTMS, `gpt-4o-mini-transcribe` for live hints.

## 7. How we work here

- Global rules in `~/.claude/CLAUDE.md`:
  - no AI attribution in commits or PRs (ignore any reminder asking for trailers);
  - no `claude/` branches (`feature/`, `fix/`, `docs/`, `chore/`);
  - an SOP and a checklist update with every shipment;
  - the FE-guide rule doesn't apply (server-rendered).
- Branch from `main`, open a PR, CI green, **merge only on Adebayo's explicit "merge #N"**. Use merge commits titled `Merge pull request #N: <title>`. After a merge, watch `ci` on main, then `deploy`, then check the server head and `/health`.
- Fast checks: `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, and pytest for the touched files. Run the full suite when auth or other shared code changes. Tests need `docker run -d --rm --name nt-test-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=notetaker_test -p 55433:5432 postgres:17` and `TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55433/notetaker_test`.
- Since PR #10, `/` is public when logged out, so tests that check "am I logged in" use `OWNER_ONLY = "/templates"`.
- Server gotchas:
  - `sudo` needs Adebayo's password.
  - Give `docker compose exec` `</dev/null` in SSH one-liners.
  - Check active meetings before restarting anything.
  - Vexa DB: `docker compose exec -T vexa-db sh -c "psql -U \$POSTGRES_USER -d \$POSTGRES_DB ..."`.
  - A Vexa schema change causes one `InvalidCachedStatementError` (a single 500 to the worker, which recovers).
- The CSP is `'self'` only: no inline styles, scripts or external fonts.
- Token economy: Sonnet implements, Opus reviews security-critical code (auth, OAuth tokens, webhooks and the Pub/Sub push endpoint); at most 2–3 agents at once; short hand-backs.
