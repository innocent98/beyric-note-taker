# Start an instant meeting: can Beyric make a Meet for a personal Gmail, and can the bot get in?

**Date written:** 2026-11-03. **Type:** small test with throwaway scripts. Nothing here touches `app/`, the production server or the production Google project. Separate from the parked instant-Meet notification test (P0.2, draft PR #46), which this does not touch.

## What this test is for

We want a "Start a meeting with the note taker" button: one tap makes a Google Meet and sends the bot into it. This test finds out whether Google lets a personal Gmail create a meeting from code, and whether the bot can then get into that meeting, with or without someone letting it in.

It decides one thing: **do we build that button (task I6 in the plan) for personal Gmail users, and do we add one more Google permission (`meetings.space.created`) to the list we send Google for verification (Phase V).**

## Result

| Question | Answer |
|---|---|
| Can a personal Gmail create a meeting through the Meet API (`spaces.create`)? | **Not run yet.** Needs Adebayo's sign-in and Allow click |
| Does the bot get in to a meeting made with Google's default settings? Did it have to knock, and did Adebayo have to admit it? | **Not run yet** |
| What does `config.accessType` come back as for the default, and for a space asked to be `OPEN`? Does an `OPEN` space let the bot in without knocking? | **Not run yet** |
| Which account types may create (personal Gmail, Workspace)? | Gmail: not run yet. Workspace: not run yet (optional, only if he has one). What Google's docs hint is in "What I read" below, **unverified** |
| Did enabling the Meet API in the console demand billing? | **Not run yet** |

**Decision: not decided yet** (test not run). The table in "What each result means" says how the answers turn into a decision.

## Adebayo: I can do the console steps for you

Steps 1 to 4 are Google Cloud console clicking. I can do them in your Chrome while you watch. **You would do only three things:** sign in to Google, handle any billing page if one appears (I will stop and tell you instead of going on), and click **Allow** on the permission page in step 5. Say "do the console steps" and I will start. If you prefer to click yourself, the steps below say exactly what to click and what you should see.

## Steps (about 40 minutes the first time)

Use your **personal Gmail** for everything. Never paste the client secret, a token or the Vexa key into the chat.

### Part 1: the Google Cloud console

1. **Make a new, throwaway project.** Open console.cloud.google.com. Click the project name in the top bar (next to the Google Cloud logo), then **New project**. Name it `beyric-meet-test`. Click **Create**, wait for the bell icon to say it is done, then pick the project in the top bar.
   *You should see:* `beyric-meet-test` in the top bar. **It must not be the production project.** Check the name before every step below.

2. **Switch on the Meet API.** Left menu (the three lines), **APIs & Services**, **Library**. Type `Google Meet REST API`, open it, click **Enable**.
   *You should see:* a page headed "API enabled" with Metrics and Quotas tabs.
   **If the page asks you to enable billing or add a payment method: stop here.** Do not enter anything. Take a screenshot and tell me the exact words and which button you were about to press. That is a result of this test (the answer to "did enabling the API demand billing"), and this test never uses billing.

3. **The permission screen, in Testing mode.** Left menu, **APIs & Services**, **OAuth consent screen** (it may be named **Google Auth platform**).
   1. Click **Get started**. App name `Beyric meet test`. Support email: your Gmail. **Next**.
   2. Audience: **External**. **Next**. Contact email: your Gmail. **Next**. Tick the policy box. **Continue**, then **Create**.
   3. Left side, **Audience**. It must say **Testing**. Under **Test users** click **Add users**, type your Gmail, **Save**.
   4. Left side, **Data access**, **Add or remove scopes**. In "Manually add scopes" paste `https://www.googleapis.com/auth/meetings.space.created`, click **Add to table**, **Update**, **Save**. (If you cannot find it, skip this; the script asks for it anyway.)
   *You should see:* your Gmail listed under Test users, status Testing.

4. **The sign-in key (OAuth client).** Left side, **Clients**, **Create client**, type **Desktop app**, name `meet-test-desktop`, **Create**. Click **Download JSON**. Move the file to exactly this place and rename it `client_secret.json`:

   ```
   /Users/adebayovictor/Documents/dev/beyric-note-taker/.claude/worktrees/focused-allen-1741e7/docs/spike/start-instant-meeting/local/client_secret.json
   ```

   (The `local` folder is git-ignored. Run `git status` in that folder afterwards: `client_secret.json` must not be listed.)

### Part 2: make a meeting as yourself

5. **Sign in once and click Allow.** In a terminal:

   ```bash
   cd /Users/adebayovictor/Documents/dev/beyric-note-taker/.claude/worktrees/focused-allen-1741e7/docs/spike/start-instant-meeting
   ```

   ```bash
   python3 auth.py
   ```

   A Google page opens. Pick your Gmail. You will see **"Google hasn't verified this app"**: click **Advanced**, then **Go to Beyric meet test (unsafe)**. On the permissions page leave every box ticked and click **Continue** (or **Allow**).
   *You should see in the terminal:* "Saved the token", a list of granted permissions that includes `.../auth/meetings.space.created`, and an "Account type:" line. Paste that terminal output into the chat (it holds no secret; the script never prints a token or your address).

6. **Create a meeting with Google's default settings.**

   ```bash
   python3 create_space.py
   ```

   *If it works you see:* "Google CREATED a meeting for this account", a `https://meet.google.com/...` link, and two lines: the access type you asked for (nothing) and the access type Google reports. *If Google refuses* you see "STOP" and Google's own words. Either result is an answer: paste it into the chat and skip to step 12.

7. **Join as the host.** Open the link in Chrome, signed in as the same Gmail, click **Join now**, and stay in the meeting.

### Part 3: send the bot from your Mac

8. **Start Vexa on the Mac** (the test copy in `spike/vexa`, never the production server). In `/Users/adebayovictor/Documents/dev/beyric-note-taker/spike/vexa/.env` set `IMAGE_TAG=v0.12.29` (write down what it said before; it was `v012`). Then:

   ```bash
   make -C /Users/adebayovictor/Documents/dev/beyric-note-taker/spike/vexa/deploy/lite up
   ```

   *You should see* (after a minute or two) `vexa-lite` in `docker ps --format '{{.Names}} {{.Image}}'`, with the image ending `v0.12.29`. Docker needs about 3 GB free.

9. **Send the bot into your meeting.** Use the code from the link (the part after `meet.google.com/`):

   ```bash
   python3 send_bot.py abc-defg-hij
   ```

   Watch the meeting window. The script follows the bot for up to 3 minutes, then stops it and asks two questions: did you see a "wants to join" request, and did you have to click Admit. Answer honestly; **your answer counts more than the script's reading.** Paste the whole terminal output into the chat.

10. **Wait 10 minutes.** Do not send another bot sooner: five fast knocks from one machine may be what upset Google last time.

11. **Same again, but ask for an open meeting.**

    ```bash
    python3 create_space.py --access open
    ```

    Read the two access-type lines. If Google reports `OPEN`, it accepted. If it reports something else or refuses, paste it (that is a finding). Join as host (step 7), then send the bot (step 9) with the new code. Compare: with an `OPEN` meeting, did the bot get in with no "wants to join" request?

12. **Optional, only if you have a Google Workspace account.** Add it as a test user (step 3.3), then repeat steps 5 to 7 with `SPIKE_ACCOUNT=ws` in front of each command, for example `SPIKE_ACCOUNT=ws python3 auth.py`. It keeps its own token. This tells us whether Workspace behaves differently from Gmail.

13. **Tidy up.**

    ```bash
    make -C /Users/adebayovictor/Documents/dev/beyric-note-taker/spike/vexa/deploy/lite down
    ```

    Put `IMAGE_TAG` in `spike/vexa/.env` back to what you wrote down. Then remove the app from your account (myaccount.google.com/permissions, "Beyric meet test", Remove access), and delete the console project (Settings, **Shut down**) when I say the result is recorded.

**What to send me:** the terminal output of steps 5, 6, 9 and 11 (and step 12 if run), any screenshot from step 2 if billing showed up, and your own answers about knocking. I then fill in the Result table above and the decision line.

## What each result means

| What we find | Decision |
|---|---|
| Gmail can create, and the bot gets in (by knocking and being admitted, or by an open meeting) | **Build the button in I6.** Add `meetings.space.created` to the Phase V scope list |
| Gmail can create, but the bot is refused in both the default and the open meeting | Do not add the scope yet. A button that makes a meeting the bot cannot enter is worse than none. Decide with Adebayo after the join-refusal work lands |
| Only Workspace can create | **Not for personal Gmail.** Build the button for Workspace accounts only, or skip it; the scope is added only if it is built |
| Nobody can create, or the console demands billing to enable the API | **Do not build it.** I6 delivers only the phone share-target note. No scope |

On `accessType`: Google's reference says `OPEN` lets anyone with the link in without knocking, `TRUSTED` lets the host's organisation and invited people in, `RESTRICTED` only invitees; with nothing set, a consumer account gets Google's default (the reference says `RESTRICTED` or the admin's setting). If the bot gets into an `OPEN` space without a knock, that is a way past the "Ask to join" refusals, **for meetings Beyric creates only** (it cannot change meetings other people host), and it has a cost: anyone who gets the link can also walk in. Record what happens; promise nothing.

## What I read, and what is verified

| Claim | Source | Status |
|---|---|---|
| `spaces.create` is `POST https://meet.googleapis.com/v2/spaces`, scope `meetings.space.created` only; `spaces.get` also accepts `meetings.space.readonly` | Meet REST discovery document, revision 20261029, read 2026-11-03 | Read, not run |
| `config.accessType` is `OPEN`, `TRUSTED` or `RESTRICTED`; the configured value is what comes back | same | Read, not run |
| The Meet REST quickstart lists "a Google Workspace account with Google Meet enabled" as a prerequisite | Google's Python quickstart page, read 2026-11-03 | Read. **Does not say a personal Gmail cannot create**; spec F3 and plan I6 carry the same worry, unverified. Only the run settles it |
| Enabling the Meet API needs billing | no source says so | **Unknown**; the step 2 note records exactly where, if it happens |
| The scripts parse a Space answer, explain Google's errors in plain words, never print a secret, refuse any Vexa that is not on this Mac, and read the bot's status timeline correctly (checked against the real E2 captures B1 and B2) | `tests/test_spike_start_instant_meeting.py`, 45 tests, no Google account needed | **Verified** (offline) |
| `send_bot.py` follows a bot, stops it and records the result | run end to end against a fake local Vexa server (not the real stack) | Verified against the fake only |
| Any live Google call, the real Vexa stack, and everything in the Result table | needs Adebayo | **Not verified** |

## The files

All in [`start-instant-meeting/`](start-instant-meeting/). Standard-library Python 3 only, nothing to install.

| File | Does |
|---|---|
| `auth.py` | sign in through Google's own page, save a token in `local/` (owner-only, git-ignored), say which permissions were granted and what kind of account it is |
| `create_space.py` | `spaces.create` (`--access open\|trusted\|restricted`), print the link and the access type asked for versus reported; `--get spaces/XXXX` reads one back |
| `send_bot.py` | send the bot from the Mac's Vexa (`http://localhost` only), follow its status, stop it, ask the two host questions, save `local/bot-<code>.txt` |
| `instant_common.py` | the shared parts: reading a space, plain error text, redaction, the localhost guard, the timeline reader |

Secrets: `client_secret.json`, `token-*.json`, logs and bot records live only in `local/` (git-ignored; `git check-ignore` confirmed). The Vexa key is read from `spike/.env.api` at run time and never printed.

## For the Phase V scope list

**Add `https://www.googleapis.com/auth/meetings.space.created` (sensitive) only if the decision above is "build the button".** The one feature that uses it is "Start a meeting with the note taker"; the screen that shows the use is that button and its consent line. The list must be frozen before the single submission (a scope added after approval needs a new review), so settle this test before V0a. If the decision is "do not build", the list stays as it is.

## Follow-ups

- Run the test (needs Adebayo), fill in the Result table and the decision line, and update the checklist line.
- If the decision is "build": task I6 builds `app/meet_manual.py` and `POST /api/meetings/start-instant`; this test's access-type finding picks the `accessType` the button asks for.
- Delete the throwaway project and the `local/` folder when the result is recorded.
