# SOP: Privacy Policy and Terms wording for multi-team

**What shipped.** New wording in the Privacy Policy and the Terms for multi-team (a personal space, several teams, Share and Move, a moved meeting that outlives its recorder's account), the "Last updated" date of both pages set to 14 November 2026, and `TERMS_VERSION` moved to match. Branch `feature/legal-wording-teams`; PR number in the PR list (opened 2026-11-14, **not to be merged until the owner's lawyer has read it**).

**The words are the owner's, approved 2026-11-14.** They were fitted into the existing markup and not reworded. The lawyer reads the PR before it merges; the lead holds the merge.

## Why

Both pages said that closing an account deletes the person's meetings. For a meeting moved to a team that stops being true (spec `docs/superpowers/specs/2026-11-13-multi-team-design.md` sections 6.4, 9.1: the team owns it, the recorder becomes a "Former member"). Google compares the privacy policy with the app, and a user is entitled to the truth, so the pages must change before `MULTI_TEAM_ENABLED` is switched on.

## What changed (exact sentences)

Both the React pages and the Jinja templates they replace (`web/tests/legal.test.tsx` fails if their text differs), so four files carry every change.

**Privacy, `<h3>Teams</h3>` paragraph**

- Old: "People you invite to your team can see tasks assigned to them, including a short quote from the meeting, and any meeting you choose to share with the team. Meetings are private to you unless you share them. We keep the name and email address you enter for an invitation until it is accepted, cancelled or expires."
- New: "You have a personal space that only you can use. You can also create teams and be invited to teams, and you can belong to several teams at once. People in a team can see tasks assigned to them, including a short quote from the meeting, and any meeting that is shared with, or moved to, that team. Meetings are private to you unless you share or move them. When you share a meeting with a team, its members can read it, and you can stop sharing at any time. When you move a meeting to a team, the meeting belongs to the team: its members can read it, and the team's owners and admins can change or delete it. A moved meeting stays with the team even if you leave the team or close your account. If you are the only member of a team when you close your account, the team and the meetings moved to it are deleted with your account. If you close your account, your name and email address are removed from it and it is shown as from a "Former member". The app asks you to confirm before you move a meeting. We keep the name and email address you enter for an invitation until it is accepted, cancelled or expires."

**Privacy, retention list, the "Transcripts and notes" item.** Existing sentences kept; added at the end of the same item (the sole-member sentence was added later, see below): "Meetings you moved to a team are an exception: they stay with the team when you close your account, until the team's owner or an admin deletes them, or the team is deleted. A team whose only member closes their account is deleted with it, and so are the meetings moved to it. If a team is deleted, a meeting moved to it goes back to the person who recorded it if their account still exists, and is otherwise deleted with the team. Meetings you only shared with a team are deleted when you close your account, as before."

**Privacy, retention list, the "Account information" item.** Existing sentence kept; added: "When an account is closed, the account is erased. We keep only an empty record with no name or email address, so that meetings moved to a team remain linked to a "Former member"."

**Terms, "Your content"**

- Old: "You are responsible for who you invite to your team and for what you choose to share with them."
- New: "You are responsible for who you invite to a team, what you share with it and what you move to it. A meeting you move to a team belongs to that team: you cannot take it back yourself, and it stays with the team if you leave it or close your account. Ask a team owner or admin to move it back."

**Terms, "Suspension and termination", the closing paragraph.** Added at its end: "This does not apply to meetings you moved to a team, which stay with the team as described in the Privacy Policy."

**Dates.** "Last updated 4 November 2026" became "Last updated 14 November 2026" on both pages. "Effective 2 November 2026" is unchanged. `RECORDING_ACK_VERSION` is unchanged.

### Sole-member team sentences, added by the owner (2026-11-14, after the first draft)

The first draft of this SOP flagged that a team whose only member closes their account is deleted with it, and so are its moved meetings, while the approved Teams paragraph had no exception. **The owner decided to add two sentences** (his words in chat: "Yes, add it"):

- Privacy, Teams paragraph, directly after "A moved meeting stays with the team even if you leave the team or close your account.": "If you are the only member of a team when you close your account, the team and the meetings moved to it are deleted with your account."
- Privacy, retention list, moved-meetings item, directly after "...until the team's owner or an admin deletes them, or the team is deleted.": "A team whose only member closes their account is deleted with it, and so are the meetings moved to it."

The text now matches `teams.doomed_teams` and `deletion.meetings_to_delete`. The lawyer is to confirm the two sentences. (The Terms sentence "it stays with the team if you leave it or close your account" has no such exception; the lawyer may want one there too.)

### `TERMS_VERSION` moved (a deviation to confirm)

The brief said to leave `TERMS_VERSION` (`app/signup.py:43`) alone. `tests/test_legal.py::test_the_terms_version_is_the_last_updated_date_on_both_pages` fails when the pages' date and `TERMS_VERSION` differ (it is the guard that the version recorded at sign-up is the date the pages show), so changing the date alone turns CI red. I moved `TERMS_VERSION` from `"2026-11-04"` to `"2026-11-14"`. Nothing reads the value to gate anything: it is only written to `users.terms_version` when an account is created (`app/signup.py`, `app/google_signin.py`) and read back by tests. So **no existing account is asked to accept again and nobody is notified**, which is the owner's decision. The only effect is that accounts made after the deploy record `2026-11-14`. If the lead would rather keep the old value, the alternative is to change the date guard in that test, which weakens it; revert the one line in `app/signup.py` and the test together.

## What the lawyer must confirm

1. The wording above as a whole, in particular that a moved meeting is the team's and survives the recorder's account.
2. That "your name and email address are removed" is enough for a kept moved meeting, given what deletion keeps (next section). The pages do not list these items; the owner chose that.
3. **Where the approved text and the code differ** (also told to the lead):
   - *A team the closing account owns alone.* **Resolved by the owner's added sentences (section above); lawyer to confirm.** The code deletes such a team with the account (`teams.doomed_teams`, `teams.remove_users`), and a meeting moved to it with it (`deletion.meetings_to_delete`).
   - *"Empty record".* The kept row has no name, email, password or Google id, and its email is replaced by the placeholder `former-member-<id>@invalid.invalid`. It also still carries the account's id and `created_at`, `last_login_at`, `deleted_at`, `purged_at`, `status`, the recording-consent time and wording version (`recording_ack_at`, `recording_ack_version`), and the `session_epoch` counter (`app/deletion.py::erase_account_row`). None of these is a name or email address, but "empty" is not literally true.
   - *"The app asks you to confirm before you move a meeting"* is the stage 3 frontend (PR #190), not on `main`. The branch has a "Move meeting" confirmation (`web/components/app/meeting-team-card.tsx`); it was read, not run. The sentence is true only once #190 is deployed, so the order is: #190 live, this PR, then the switch.

## What deletion keeps about the person on a kept moved meeting

Stated from the code (`app/deletion.py`, `app/pipeline.py`, `docs/sop/2026-11-14-multi-team-stage-2.md` follow-up 12). For the lawyer; **not** on the pages.

| Kept | Detail |
|---|---|
| The meeting itself | transcript, notes, summaries, decisions, open questions, review items, tasks (including who they were assigned to), title, date, platform |
| Other people's names | the display names of participants and who was speaking, inside the transcript and speaker text |
| The meeting link | `meetings.url`, which can be the person's personal meeting room |
| Audio, for the normal period only | `purge_user` does not touch a kept meeting's audio (`meetings_to_delete` excludes it). The ordinary sweep (`pipeline.delete_expired_audio`, by the meeting's age, `AUDIO_RETENTION_DAYS`, 7 today) still deletes the audio and the provider's copy; closing an account soon after a meeting leaves them until then |
| The provider's copy, for the same period | Vexa or Skribby recording and bot record; removed by the same sweep |
| A notes template the meeting names | the template's text, kept and soft-deleted (`deleted_at`), owned by the hollow row, because a meeting must name its template; any template no kept meeting names is deleted |
| The hollow user row | id, placeholder email, `created_at`, `last_login_at`, `deleted_at`, `purged_at`, `status`, `recording_ack_at`, `recording_ack_version`, `session_epoch` (see above) |
| The hours | `usage_entries` with seconds and a one-way email fingerprint, as already stated in the policy |

Not kept (deleted by `erase_account_row` or `purge_user`): name, email, password hash, Google id, memberships, notifications, queued mail, settings, vocabulary, waiting-list row, invitations, calendar connections, the record and recipients of notes the person emailed (`meeting_shares` cascades on the sender), unconfirmed date proposals from their meetings, and tasks they typed in themselves.

## What's involved

- `web/components/legal/privacy-content.tsx`, `web/components/legal/terms-content.tsx` (heading ids and `web/components/legal/toc.ts` unchanged; no heading added).
- `app/templates/privacy.html`, `app/templates/terms.html`: the same text, apostrophes plain (the React pages use `&apos;`).
- `app/signup.py`: `TERMS_VERSION`.
- `tests/test_legal.py`: new `test_the_pages_say_what_happens_to_a_meeting_moved_to_a_team`.
- No migration, no config, no API change. No hard-coded date elsewhere: `web/lib/site.ts`, the sitemap and the JSON-LD do not carry it.

## Verification

On the Mac, bounded: `npm run lint` (0 errors; 3 warnings in files this work did not touch), `npm run typecheck`, `npx vitest run tests/legal.test.tsx` (7 tests: React text equals the Jinja template, anchors and links equal), and, with the pinned `ruff==0.17.3`, `ruff check .` and `ruff format --check .`. `tests/test_legal.py` needs the app's Postgres fixtures and was **not run locally**: CI runs it. Not run: the full suites, e2e, the Next build.

## Operate / roll back

**`MULTI_TEAM_ENABLED` must stay off until the lawyer has read this, this PR is merged and deployed, and PR #190 (the frontend) is live.** The pages are plain text with no flag; they go live on deploy. Roll back by reverting the PR (no data, no migration). If `TERMS_VERSION` is reverted, revert the date on both pages or the date guard fails.

## Follow-ups

- Lawyer's reading and any change the lawyer asks for (reword in the four files together; the parity test keeps them in step).
- Decide whether "empty record" needs a clause, and whether the Terms sentence needs the sole-member exception.
- The user-visible deletion promise in the app itself (Settings, account closing) should be re-read once #190 is live for the same sentence.
