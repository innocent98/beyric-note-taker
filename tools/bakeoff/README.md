# Bot bake-off tool

## What this is for

Google refuses our own bot (Vexa) about a quarter of the time. Before paying anyone, we want
numbers: on the same kind of real Google Meet calls, how often does each bot **get in**, how often
does it have to **knock** (wait at the door for someone to press Admit), and how often does Google
**refuse** it?

This tool sends a bot from **Skribby** or **Recall** into a Meet you start yourself, watches what
happens, makes the bot leave after about a minute (to save free hours), and writes one line per
trial to a log file. For **Vexa** it cannot send the bot (that stays in the Beyric app), so you
tell it what you saw with one short command. Then `report` adds it all up.

What it will never do: it runs on your Mac only, talks only to Skribby's and Recall's servers, never
contacts notes.beyrictech.com, never reads Beyric data, and never prints or saves your keys.
Nothing is deployed to the server.

You need Python 3 (any version from 3.9 up; `python3 --version` shows it). Nothing to install.

## The rules of the experiment (so the numbers mean something)

1. **One bot per meeting.** Start a new Meet for every trial. Recall's own docs say a bot leaves if
   another participant's name contains "notetaker", "recorder" or "assistant", so two bots in one
   call can make each other leave. Never put two arms in one call.
2. Each arm uses a plain, different, human-looking name: Skribby guest "Ada W", Recall guest
   "Sam K". The signed-in Skribby bot shows the Google account's own name, not a name we choose.
3. Press **Admit** in about the same way for every arm (when you notice the knock, not faster for
   one vendor). If you were slow, say so in `--note "admitted late"`.
4. Say who hosts: `--host-type self` (you start the call from your own account), `other` (someone
   else in your organisation hosts), `external` (someone from another organisation hosts). Keep a
   similar mix across arms, and say in `--note` if the call's Meeting access is Open, Trusted or
   Restricted, and whether the Waiting room is on.
5. Aim for **10 trials per arm at least**. Ten per arm only shows a big gap, not a small one; the
   report says how big a gap has to be before it counts.

## Step 1: Skribby (free plan)

1. Go to https://platform.skribby.io and create an account on the **Free** plan.
2. In the dashboard open **API Keys**, generate a key and copy it (it is a secret: do **not** paste
   it in chat).
3. Add the bot's Google account (needed only for the `skribby-auth` arm). Use a **dedicated**
   Google account, not a personal one. It must have 2-Step Verification switched on, with an
   authenticator app (app passwords do not work). Open **Settings > Authenticated Accounts > Add
   Account**, choose Google Meet, and enter the email, the password and the **authenticator setup
   key**. The setup key is the long text code Google shows when you add an authenticator app
   (under "can't scan it? enter a setup key"). It is **not** the six-digit number that changes
   every 30 seconds. Save, then copy the **account id** that Skribby shows.
4. If your Skribby account is in the Japan region, also add `SKRIBBY_REGION=jp` in step 3.

**What Skribby's pages do NOT say (read 2026-11-07, saved in `docs/spike/primary-pages-2026-11-07/`):**
- That **Authenticated Accounts work on the Free plan**. The billing page lists the Free plan's
  limits (5 bot hours a month, 3 bots at once, audio only, no recording upload) and says nothing
  about accounts either way; the authentication page lists a concurrency number for Free but never
  says accounts are included. We find out at the first live check of `skribby-auth`. If the
  dashboard will not let you add an account, or the run says it needs a paid plan, write down the
  exact words and tell the lead; do not guess.
- That the transcription model `none` (which the models page calls "No Transcription") is what the
  Free plan calls "Audio Only". The tool sends `none`; if Skribby refuses it, run with
  `--skribby-model groq/whisper-large-v3-turbo`, the one model the Free plan names.
- How many authenticated accounts the Free plan allows.
- How `bot_detected` retries are timed. The pages say up to 3 retries.

## Step 2: Recall

1. Pick a region and sign up there (the sign-up links are on Recall's regions page): US West
   https://us-west-2.recall.ai/auth/signup, EU https://eu-central-1.recall.ai/auth/signup, or Asia
   https://ap-northeast-1.recall.ai/auth/signup. Recall's regions page says all four regions work
   with pay-as-you-go, and that an account only works in the region where it was made. Note the
   region name (for example `us-west-2`): it is the first part of the web address you land on.
2. In the dashboard open **Developers > API Keys** (for US West:
   https://us-west-2.recall.ai/dashboard/developers/api-keys), create a key and copy it. Also a
   secret: do **not** paste it in chat.
3. Only `recall-guest` is in this bake-off. A signed-in Recall bot needs a Google Workspace with
   Legacy SSO and is out of scope.

## Step 3: put the keys in a file outside the repo

```bash
mkdir -p ~/.config/beyric-bakeoff
```

```bash
open -e ~/.config/beyric-bakeoff/keys.env
```

Type this into the file that opens (replace each `paste-...` with your own value, no quotes), save,
and close it:

```
SKRIBBY_API_KEY=paste-your-skribby-key-here
SKRIBBY_ACCOUNT_ID=paste-the-skribby-account-id-here
RECALL_API_KEY=paste-your-recall-key-here
RECALL_REGION=us-west-2
```

```bash
chmod 600 ~/.config/beyric-bakeoff/keys.env
```

(The same names also work as environment variables, and those win over the file.) The file is not
in the repo and the tool never prints a key. `tools/bakeoff/logs/` and any `.env` file in
`tools/bakeoff/` are git-ignored anyway.

## Step 4: the first live check (about 5 minutes, about 4 minutes of free time)

Nothing here has been run against a real vendor yet: the tool was built from the vendors' own
pages and tested with a pretend network. This check is the first real run, so read what comes back
and tell the lead anything odd.

1. Open https://meet.google.com, choose **New meeting > Start an instant meeting**, and copy the
   link (it looks like `https://meet.google.com/abc-defg-hij`). Stay in that call: **you are the
   host and you press Admit**.
2. See what the tool would send, without sending anything:

   ```bash
   python3 tools/bakeoff/bakeoff.py run --arm recall-guest --meet-url PASTE-THE-LINK --dry-run
   ```

   You should see a web address, `Authorization: [redacted]` and the request body. No key appears.
3. Send the real bot:

   ```bash
   python3 tools/bakeoff/bakeoff.py run --arm recall-guest --meet-url PASTE-THE-LINK --host-type self --note "first live check"
   ```

   "Sam K" asks to join; press **Admit**. The screen prints each status with the seconds since the
   start. About a minute after it is in, the tool asks the vendor to make the bot leave and prints
   the result and where it logged it.
4. End that call. Start a **new** instant meeting and do the same for Skribby (its bot is "Ada W"):

   ```bash
   python3 tools/bakeoff/bakeoff.py run --arm skribby-guest --meet-url PASTE-THE-NEW-LINK --host-type self --note "first live check"
   ```

5. Run `python3 tools/bakeoff/bakeoff.py report`. Send the lead the printed output and the lines of
   `tools/bakeoff/logs/bakeoff.csv` (they hold no keys and only the last four letters of the call
   code).

| What you see | What it means |
|---|---|
| `Problem: RECALL_API_KEY is missing` | The keys file is not where the tool looks, or that line is empty (step 3). |
| `Could not start the bot: HTTP 401` | The key is wrong or from another region (Recall: key and region must match). |
| `HTTP 403` from Skribby | Probably "limit of concurrent meeting bots" (Free plan: 3 at once). Wait for earlier trials to end. |
| `HTTP 403 request_blocked` from Recall | Recall's firewall refused the request (the page says a VPN or shared network can cause it). Try without the VPN. |
| `Result: error (create-failed)` | The vendor refused to make the bot; the reason is in the notes column. Not counted as a Google refusal. |
| It sits at `joining` or `in_waiting_room` | Press Admit in the call. After 10 minutes (the `--lobby-limit`) the tool gives up and logs `never-joined`. |

If anything hangs, press Ctrl-C once: the tool asks the vendor to make the bot leave (so it does
not run up hours), logs the trial as an error, and exits.

## Step 5: running real trials

### Skribby and Recall guests (`skribby-guest`, `recall-guest`)

Start a new Meet, copy the link, run (one command per trial, one arm per meeting):

```bash
python3 tools/bakeoff/bakeoff.py run --arm recall-guest --meet-url LINK --host-type self
```

Useful options: `--stay 60` (seconds in the call before the bot is asked to leave),
`--lobby-limit 600` (seconds at the door before giving up), `--note "text"`, `--dry-run`.

### Skribby signed in (`skribby-auth`): invite the bot's Google account first

The signed-in bot only skips the knock if its Google account is on the calendar event.

1. Open Google Calendar, create an event, and add **Google Meet video conferencing**.
2. Under **Add guests**, type the **bot account's email** (the one from Step 1) and press Enter.
   Save the event. Open it again and check the account is in the guest list. (An email sent later,
   during the call, does not count; Recall's own page says so.)
3. Copy the Meet link from the event. Open the call yourself, then run:

   ```bash
   python3 tools/bakeoff/bakeoff.py run --arm skribby-auth --meet-url LINK --host-type self --note "invited, Meeting access Trusted"
   ```

   The bot shows the account's own name. If it still knocks, that is a result: it is logged as
   `knocked-then-admitted` (Skribby's docs say the host's meeting settings decide).

### Vexa (our own bot): start it in the Beyric app, then tell the tool what you saw

Start the bot from the Beyric app as usual, in its own new Meet. Watch it, then record the outcome:

```bash
python3 tools/bakeoff/bakeoff.py note --arm vexa --meet-url LINK --result knocked-then-admitted --seconds-to-door 47 --host-type self
```

`--result` is one of `admitted` (walked straight in), `knocked-then-admitted` (waited at the door,
then you admitted it), `refused` (Google or the host said no; add `--reason "what you saw"`), or
`never-joined` (nothing happened and you gave up). `--seconds-to-door` is the number of seconds
from pressing start in the Beyric app until the bot was in the call (for a refusal, until you saw
the refusal; it goes in the notes). This one is typed by hand, so it is only as accurate as your
stopwatch.

### Free hours

Skribby's Free plan gives 5 bot hours a month; Recall's first 5 hours are free. The tool adds up the
bot minutes in the log per vendor and **refuses to start a new trial at 4.5 hours** for that vendor
(`--cap-hours 4` to change it; `--force` to override). Each trial costs about 2 minutes, so this is
roughly 100 trials. Skribby's hours renew each month: from the next month, use
`--budget-since 2026-12-01` (the date your Skribby month restarts). The tool counts conservatively
(whole minutes, and waiting at the door as billed); the vendor's dashboard is the real bill.

## Step 6: reading `report`

```bash
python3 tools/bakeoff/bakeoff.py report
```

Example (made-up rows, to show the layout):

```
recall-guest
  trials: 3 (3 counted, 0 errors on our side or the vendor's API)
  admitted: 2 of 3 = 67% (true rate could be anywhere from 21% to 94%)
  needed a knock: 1 of 2 admitted (seen as a waiting-room status)
  seconds to admission: median 20, slowest 31
  refused: fatal/google_meet_bot_blocked x1
```

| Line | Meaning |
|---|---|
| trials / counted / errors | `counted` leaves out trials that failed on our side or at the vendor's API (a refused create, bad key). They are not Google's answer, so they do not lower a vendor's rate. They are still listed. |
| admitted | Trials where the bot got into the call, out of the counted ones. The range is how wrong a small sample can be. |
| needed a knock | Admitted trials where the bot first waited at the door. **Recall:** seen exactly (a waiting-room status). **Skribby:** Skribby has no waiting-room status (its docs put the waiting room inside `joining`), so a knock is **inferred**: 20 seconds or more in `joining` before it got in (`--knock-threshold` to change). Treat the Skribby number as a good guess. **Vexa:** what you typed. |
| seconds to admission | Median and slowest time from the vendor starting the bot to the bot being in the call. Measured from the vendor's own timestamps, so a slow status check does not change it. |
| refused / never joined / errors | The reasons, counted. Refused means Google or the host said no (for example `google_meet_bot_blocked`, `request_denied`, `bot_detected`). Never joined means it waited at the door until the limit. |
| Bot minutes | Time used per vendor against the cap. |
| In plain words | How big a gap between two arms has to be before it is more than luck, for the size of your smallest arm. |

## Files

| File | What it is |
|---|---|
| `tools/bakeoff/bakeoff.py` | the tool (one file, standard library only) |
| `tools/bakeoff/logs/bakeoff.csv` | the log, one line per trial, only ever added to; created on first use; not in git |
| `tests/test_bakeoff_tool.py` | the tests (a pretend network; one test opens a socket to this computer only) |
| `docs/spike/primary-pages-2026-11-07/` | the vendor pages the tool was built from |
| `docs/sop/2026-11-07-bot-bakeoff-tool.md` | what shipped, decisions, what is unverified |

CSV columns: `time_utc, time_lagos, trial_id, arm, host_type, meeting_code, result, seconds_to_lobby,
seconds_to_admission, final_status, bot_minutes, notes`. The meeting code keeps only its last four
letters, without the dashes. `seconds_to_lobby` is filled for Recall only (Skribby does not say
when the lobby starts). The `notes` column holds the full status timeline, for example
`timeline booting+0s joining+6s recording+40s`.

To run the tests: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_bakeoff_tool.py`.
