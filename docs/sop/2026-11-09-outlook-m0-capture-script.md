# SOP: Outlook phase O, M0 capture script (keys file and pasted redirect)

## What shipped
The M0 throwaway (`docs/spike/outlook-m0/capture.py`, first committed with M1) now runs against **the production Azure registration** as well as a local-development one, and reads its keys from one file outside the repo. Branch `feature/outlook-m0-capture`, PR to `main` (number in the PR list). No `app/` code, no migration, nothing deployed. **Nothing was run against Microsoft by the session that built it** (it needs Adebayo's browser sign-in and secret); the selftest (no network) passes.

The brief asked for `scripts/outlook_capture.py`. A complete M0 script already existed, so a second copy was not written: the existing one was extended and stays at `docs/spike/outlook-m0/` (its README, `.gitignore` entries and the spec all point there).

## Why
Adebayo registered the **production** app (spec 9.1: one Web redirect, `https://notes.beyrictech.com/api/calendar/microsoft/callback`). The script only caught the redirect on `http://localhost:8000`, which only the optional second registration has, and it read `MS_M0_*` names from a file inside the repo folder. Both stopped the run before it began.

## How
| Piece | Decision |
|---|---|
| Keys | `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_AUTHORITY` (the names the app will use). Order: process environment, then `~/.config/beyric-outlook/keys.env`, then the old `.env.m0` (`MS_M0_*`). Never from the command line, never printed; an authority outside `consumers`, `organizations`, `common` is refused |
| Redirect | `--redirect local` (default): the script listens on `localhost:8000`. `--redirect paste`: it opens the authorize URL, Adebayo copies the address bar after consenting (the production page shows an error: expected) and pastes it at a hidden prompt. `--redirect-uri` overrides the string sent to Microsoft |
| Paste checks | Only the query is read; scheme, host and path must equal the registered redirect; `state` must equal ours (constant-time compare); a `?error=` answer is recorded as an S2 fact like the local mode. The pasted address is never printed or stored |
| `--authority` | now optional (default: the keys file's value, else `consumers`) |
| Instructions | Click-by-click run steps at the top of the script and in `README.md` sections A and C |
| Rejected | A second script under `scripts/` (duplicate, and `docs/spike/` is already excluded from ruff and CI); asking Adebayo to add a `localhost` redirect to the production registration (the spec keeps production to one redirect); storing the pasted URL for replay (the code works once) |

## What's involved
`docs/spike/outlook-m0/capture.py` (`load_env`, `redirect_uri_for`, `parse_pasted_redirect`, `paste_callback`, `check_state`, selftest cases), `docs/spike/outlook-m0/README.md`, `docs/checklist/master-checklist.md` (Phase O, M0 lines). Output paths unchanged: raw (git-ignored, tokens stripped) in `docs/spike/outlook-m0/out/raw/`, redacted JSON in `docs/fe-captures/calendar-outlook/graph/`, the readable record in `docs/spike/outlook-oauth-captures.md`.

## Verification
- `python docs/spike/outlook-m0/capture.py selftest` prints `selftest ok`: redaction (addresses, GUIDs, ids, URL queries, tokens), PKCE, claim decoding, and now the pasted-redirect cases (good address, whitespace around it, another host, another path, wrong state, no state, not a URL, a `?error=` answer) and the keys-file reader.
- `ruff check .` and `ruff format --check .` pass (`spike` is excluded from the project's lint; the file was formatted and import-sorted by hand with the pinned ruff).
- **Not verified:** a real run (consent, token redemption, Graph calls). That is the whole point of M0 and waits for Adebayo.

## Operate / roll back
**Server steps before merge: none** (docs and a throwaway script). To use it, follow the steps at the top of the script: keys file, `selftest`, then one `run` per account (`--redirect paste` for the production registration). Delete `docs/spike/outlook-m0/out/` and the client secret in Entra (if it was a throwaway secret) when the runs are done. Revert the PR to go back to the earlier script.

## Follow-ups
- Adebayo runs the sessions (README section C); then M2 and M5 replace the "documented shape" fixtures with the redacted captures, and every line in their SOPs under "to confirm with the M0 run" is answered or corrected.
- If the production secret is used for the run, rotate it afterwards (spec 9.3): it was typed into a file, not a chat, but the file stays on his Mac.
