# SOP: "Start an instant meeting" test (P0.10), scripts and runbook

## What shipped
A docs-and-throwaway-scripts spike, branch `docs/p03-start-instant-meeting-test`, PR below in the report. Written and unit-tested; **the live test has not been run** (it needs Adebayo's Google sign-in and Allow click). Result doc: [`docs/spike/start-instant-meeting.md`](../spike/start-instant-meeting.md).

## Why
Spec F3 and plan I6 want a "Start a meeting with the note taker" button (`spaces.create`, scope `meetings.space.created`), but say creation may need Workspace (unverified), and the join refusals of 2026-11-03 make it unclear the bot could enter such a meeting. Separate from P0.2 (draft PR #46, parked), which tests Meet notifications and needs billing for Pub/Sub; this one needs no Pub/Sub and no billing.

## How
| Decision | Reason |
|---|---|
| Standard-library Python, no install | Adebayo is not a Google Cloud expert; fewer steps |
| Loopback OAuth with PKCE, one Meet scope plus `openid email` | the ID token's `hd` claim tells Gmail from Workspace; the address is never printed |
| Test the default access type and `OPEN` separately, read back `config.accessType` | Google may not apply what is asked; the reported value is the finding |
| `send_bot.py` talks to `http://localhost` only | the test must never send a bot from the production server |
| The host's own answer ("did you see a knock, did you click Admit") outranks the API timeline | on v0.12.28 `awaiting_admission` is not proof of a lobby; v0.12.29 is pinned for the test |
| 10 minutes between bot sends | five fast knocks may have hurt in the 2026-11-03 refusals |
| Runbook starts with what the test is for, then clicks with "you should see"; offers to do the console steps in his Chrome | Adebayo found the P0.2 runbook confusing (memory: explain plainly, offer to do it) |

Rejected: reusing P0.2's scripts (they need Pub/Sub and billing, and that branch is parked); a stub test of Google (proves nothing).

## What's involved
| Path | Purpose |
|---|---|
| `docs/spike/start-instant-meeting.md` | runbook, result table, decision table, Phase V scope note |
| `docs/spike/start-instant-meeting/{auth,create_space,send_bot,instant_common}.py` | the scripts |
| `docs/spike/start-instant-meeting/.gitignore` | keeps `local/` (client secret, tokens, bot records) out of git |
| `tests/test_spike_start_instant_meeting.py` | 45 offline tests |
| `docs/checklist/master-checklist.md` | P0.10 and the Phase V note |

No app code, migration, API or config changed, so there is no FE guide change.

## Verification
- `ruff check` and `ruff format --check` (pinned 0.17.3) clean on the new files; the repo's own CI ruff run excludes any folder named `spike`, so I ran it on these paths explicitly.
- `pytest tests/test_spike_start_instant_meeting.py`: 45 passed. The timeline reader is checked against the real E2 captures (`docs/spike/meet-join-experiment/logs/B1`, `B2`).
- `send_bot.py` run end to end against a fake local Vexa: followed the statuses, stopped the bot, saved the record; the production URL is refused.
- The Meet request shape and scopes were read from Google's discovery document (revision 20261029). **Not verified:** any live Google call, the real Vexa stack, account-type rules, whether the API demands billing.

## Operate / roll back
Nothing to deploy. Delete the throwaway console project and `local/` when the result is recorded; remove the app at myaccount.google.com/permissions.

## Follow-ups
Run the test and fill in the result and decision; if "build", I6 builds the route and the scope joins the Phase V list.
