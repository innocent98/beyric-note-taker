# Instant-Meet spike (Task P0.2): does Meet's "started" event reach a personal Gmail?

**Date:** 2026-11-03 (Part 1: scripts and runbook). **Plan:** [`P0.2`](../superpowers/plans/2026-11-03-pilot-accounts-calendar.md). **Spec:** section 8.3 of [the design](../superpowers/specs/2026-11-03-pilot-accounts-calendar-design.md). **Scripts:** [`pilot-instant-meet/`](pilot-instant-meet/). Nothing here touches `app/` or the server.

## Result (filled in Part 2)

| | Value |
|---|---|
| **Gate outcome** | **pending** (G1 to G4, see the table at the end) |
| `INSTANT_MEET_MODE` default | pending (spec default until decided: `auto`) |
| `INSTANT_MEET_PERSONAL_MODE` default | pending (spec default until decided: `off`) |
| S6 (7-day behaviour) | **pending, due 2026-11-10** (starts when step 6 of the runbook runs; the due date moves with it) |

---

## 1. Runbook for Adebayo (about 45 minutes, most of it clicking)

Use a **throwaway** Google Cloud project and a **test** Gmail account. Nothing in the production project changes. Never paste a token or the client secret into chat: the scripts keep them in a git-ignored `local/` folder and redact every log line.

Use a separate Chrome profile (or a private window) signed in **only** as the test Gmail for steps 5 and 7, so the consent and the calls are made as that account.

### 1. Create the project and attach billing
1. console.cloud.google.com, project picker (top bar), **New project**, name `beyric-meet-spike`, **Create**. Make sure it is the selected project.
2. Menu, **Billing**, link your billing account to this project. (The Events API and Pub/Sub need it; this spike costs cents.)

### 2. Enable five APIs
Menu, **APIs & Services**, **Library**; search each name, open it, click **Enable**:
- Google Workspace Events API
- Google Meet REST API
- Cloud Pub/Sub API
- People API (the spike uses it to read the user id)

(The Calendar API is not needed: you create the calendar event by hand.)

### 3. Consent screen, in Testing mode
Menu, **APIs & Services**, **OAuth consent screen** (it may be called **Google Auth platform**).
1. **Get started**. App name `Beyric spike`, user support email = any of yours. **Next**.
2. Audience: **External**. **Next**. Contact email = yours. **Next**. Tick the policy box. **Continue**, **Create**.
3. Left side, **Audience**: publishing status must read **Testing**. Under **Test users**, **Add users**, add the **test Gmail** (and, if you have a Workspace account, that address too). **Save**.
4. Left side, **Data access** (optional but tidy), **Add or remove scopes**, tick `.../auth/meetings.space.readonly` and `.../auth/pubsub`, **Update**, **Save**. If you cannot find them, skip this: the script asks for them anyway.

### 4. OAuth client, and where the file goes
1. Left side, **Clients**, **Create client**, type **Desktop app**, name `spike-desktop`, **Create**.
2. In the box that appears click **Download JSON** (the secret is shown only now).
3. Move the file to exactly this path and rename it `client_secret.json`:

```
/Users/adebayovictor/Documents/dev/beyric-note-taker/.claude/worktrees/p02-instant-meet-spike-065f6e/docs/spike/pilot-instant-meet/local/client_secret.json
```

(The `local/` folder already exists and is git-ignored. Check with `git status`: it must not appear.)

### 5. Consent click (you do this; the script never sees your password)
In a terminal:

```bash
cd /Users/adebayovictor/Documents/dev/beyric-note-taker/.claude/worktrees/p02-instant-meet-spike-065f6e/docs/spike/pilot-instant-meet
```

```bash
python3 auth.py
```

A Google page opens. Pick the **test Gmail**. You will see **"Google hasn't verified this app"**: click **Advanced**, then **Go to Beyric spike (unsafe)**. On the permissions page **leave every box ticked** (Meet "view and download your meeting data", Pub/Sub, name/email), **Continue**. The terminal prints `Saved token.json` and the granted scopes. **Paste that terminal output back** (it holds no secret). Take a screenshot of the permissions page and note how many clicks the warning took.

### 6. Create the pipe and the subscription (S1)

```bash
python3 step0_setup.py
```

```bash
python3 step1_subscribe.py
```

The second one prints `RESULT S1: PASS` or `FAIL`. **Paste both outputs back.** If it says FAIL, still do step 7 (the S5 part needs no subscription), but skip nothing else.

### 7. Host the calls (S2, S5, S9). Keep this terminal open the whole time

```bash
python3 watch.py
```

It prints an `EVENT` line when Google delivers one and a `live records` line every 20 s. **Type a short label and press Enter at each step** (it is how I tell the calls apart afterwards). Wait about 30 s between calls so each one is its own event. Signed in as the **test Gmail**:

| Call | What to do | Label to type |
|---|---|---|
| A, scheduled | Google Calendar: new event starting now, **Add Google Meet video conferencing**, Save, open the event, **Join now**. Stay 2 min, hang up | `A calendar, in` then `A left` |
| B, instant via meet.new | Open `meet.new`, **Join now**. Stay 2 min, hang up | `B meet.new, in` then `B left` |
| C, instant in the Meet app | meet.google.com, **New meeting**, **Start an instant meeting**, join. Stay 2 min, hang up | `C instant, in` then `C left` |

Calls D and E need a **second Google account or a person who hosts for you** (the "other host"). Skip them if you have nobody; tell me and I record "not run, because ...".

| Call | What to do | Label to type |
|---|---|---|
| D, invitee | The other host makes a calendar event with Meet and **adds the test Gmail as a guest**, then starts the call. You **do not join** for 40 s, then join for 1 min, then leave | `D live, not joined` then `D joined` then `D left` |
| E, stranger | The other host starts a call (`meet.new`) and invites **nobody**. You do not join. Wait 60 s | `E live, not invited` then `E ended` |

Then press **Ctrl-C** in the terminal.

### 8. Quota, and what to send me (S7)

```bash
python3 step7_quota.py
```

(about 2 minutes). Optional but useful: Cloud console, **APIs & Services**, **Google Meet REST API**, **Quotas & System limits**: paste the text of any row mentioning "read" or "per minute".

**Tell me "calls done".** The logs are on this Mac in `local/` and I read the redacted ones. Do not paste the contents of `token.json` or `client_secret.json` anywhere, ever.

### 9. Optional: the Workspace account (S3)
Only if you have one. Add it as a test user (step 3.3), then repeat steps 5 to 7 with the same commands prefixed `SPIKE_ACCOUNT=ws `, for example `SPIKE_ACCOUNT=ws python3 auth.py`. It keeps its own token, topic and logs, so nothing mixes. If you have none, S3 is recorded "not tested".

### After the spike (not now)
Once S6 is written up, `python3 cleanup.py`, then delete the project (Cloud console, Settings, **Shut down**) and remove the app from the test account at myaccount.google.com/permissions.

---

## 2. What the scripts do

| File | Does | Spec row |
|---|---|---|
| `auth.py` | loopback OAuth with PKCE; saves `local/token.json` (mode 600); scopes: `meetings.space.readonly`, `pubsub` (spike only), openid/email/profile. `--with-created` adds `meetings.space.created` | all |
| `step0_setup.py` | topic, Publisher role for `meet-api-event-push@system.gserviceaccount.com`, **pull** subscription (7-day retention) | S1 prerequisite |
| `step1_subscribe.py` | userinfo and People id, then `subscriptions.create` on `users/{id}` trying `sub`, `people_id`, `me`, `email` in turn (an `ALREADY_EXISTS` on a later one proves two ids are the same); deletes duplicates | S1 |
| `watch.py` | pulls Pub/Sub and samples `conferenceRecords.list?filter=end_time IS NULL` every 20 s, plus your typed labels, all into `local/events.jsonl` | S2, S5, S9 |
| `step4_resolve.py` | `conferenceRecords.get` then `spaces.get` for each event; prints arrival latency and the meeting code | S4 |
| `step6_check.py` | state, `expireTime`, `suspensionReason` to `local/s6.jsonl`; `--renew` (PATCH `ttl=0s`), `--reactivate` | S6 |
| `step7_quota.py` | `conferenceRecords.list` back to back until the first 429, then waits for the window | S7 |
| `cleanup.py` | deletes subscription, pull subscription, topic | teardown |

Redaction (`common.py`): bearer and refresh tokens, `client_secret`, e-mail addresses and 13+ digit ids never reach a log; ids show as `123…45(id)`.

Checked in Part 1 against Google's discovery documents (not against a live account): `subscriptions.create` accepts `meetings.space.readonly` as well as `meetings.space.created`; `conferenceRecords.list` and `spaces.get` accept `meetings.space.readonly`; `ttl` "0" means the maximum; `reactivate` exists. A mock run of steps 1, 4 and 6 passed (candidate loop, duplicate handling, latency maths).

---

## 3. Spec 8.3 table (Part 2)

| # | Test | Result | Recorded facts |
|---|---|---|---|
| S1 | create `users/{id}` subscription | not run yet | |
| S2 | event within 60 s for calendar, meet.new, instant-in-app | not run yet | |
| S3 | Workspace account | not run yet (needs a Workspace test account; else "not tested") | |
| S4 | `conferenceRecords.get`, `spaces.get` | not run yet | |
| S5 | `list` with `end_time IS NULL`: (a) started by user, (b) other's call user joined, (c) live call user is not in | not run yet | |
| S6 | 7 days untouched | pending, due (start + 7 days) | |
| S7 | per-minute quota | not run yet | |
| S9 | invitee gets no event | not run yet | |

## 4. Raw requests and responses (Part 2)

Filled in per step from `local/log/*.log` (redacted).

## 5. Gate outcome (Part 2)

| Outcome | Condition | Phase I runs |
|---|---|---|
| G1 | S1 to S4 pass on the personal account and S9 shows no invitee delivery | I1 to I4, I7 (I6 optional); `INSTANT_MEET_PERSONAL_MODE=events` |
| G2 | passes only on the Workspace account | I1 to I4; personal accounts use `poll` if S5 allows (then I5), else `off`; I6 |
| G3 | Events fails everywhere, S5 shows (a) appears and owner is provable | I1, I5, I6, I7 (no I2 to I4) |
| G4 | nothing works | I6 and the PWA share-target note only; `INSTANT_MEET_MODE=off`; the switch stays hidden; no Meet scope is requested; no Meet row in the policy |

**Chosen:** pending.

## 6. Follow-ups
- Part 2 fills sections 3 to 5 and the Result table, marks S6, and updates the P0.2 line of `docs/checklist/master-checklist.md`.
