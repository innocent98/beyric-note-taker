# Frontend integration guide: Delete a meeting

Everything below is copied from responses captured against the running app by
`scripts/capture_meeting_delete.py` (real app under uvicorn, real migrations, real Postgres, real
HTTP; files in `docs/fe-captures/meeting-delete/NN-name.json`, cited as **#NN**). Run the script
again to re-derive any claim. Read it with `docs/fe-integration-guide-owner-app.md` (session cookie,
CSRF, error shape).

## 1. What it is, in the frontend's terms

One endpoint removes a meeting for good, with everything taken from it. The meeting page has a
**Delete meeting** control. It is **lead-designed, not in the designer's file** (the prototype has no
meeting delete); see section 6.

## 2. The `can_delete` flag

`GET /api/meetings/{id}` now carries `can_delete` (boolean, always present): true for the owner of a
meeting that is not `bot_sent` or `processing`. Ready meeting: true (#05). Bot in the call: false
(#06). Show the control only when it is true; a teammate reading a shared meeting gets false
(`tests/test_team_visibility.py`, not in these captures).

## 3. The endpoint

`DELETE /api/meetings/{id}`, owner only, `X-CSRF-Token` required like every write.

| Case | Status | Body | Capture |
|---|---|---|---|
| Deleted | 204 | none | #12 |
| No CSRF token | 403 | `{"error":{"code":"csrf_rejected","message":"Missing or wrong X-CSRF-Token header. Read it from GET /api/session."}}` | #07 |
| Bot in the call (`bot_sent`) | 409 | `{"error":{"code":"meeting_active","message":"This meeting is still being recorded or its notes are being written. Delete it once it has finished."}}` | #08 |
| Notes being written (`processing`) | 409 | the same `meeting_active` | #09 |
| Vexa would not remove the recording | 502 | `{"error":{"code":"recording_delete_failed","message":"The recording could not be removed, so nothing was deleted. Please try again in a moment."}}` | #10 |
| Already deleted, never existed, or not yours (a teammate or anyone else) | 404 | `{"error":{"code":"not_found","message":"No such meeting"}}` | #15, #21 |
| Worker holds the row | 409 `meeting_busy` | same shape as the other 409s; try again in a moment | **not captured live** (needs a race; shape from `_lock_meeting`, covered by `tests/test_end_meeting.py` for cancel) |

After a 204: `GET /api/meetings/{id}` is 404 (#14), the id is gone from `GET /api/meetings` (#16).

## 4. What goes, what stays

Row counts read from the tables before and after (#04, #11, #13):

| Gone with the meeting | Stays (no link to the meeting) |
|---|---|
| the meeting with its transcript and notes, translations, decisions, open questions, review items ("Please check"), notifications about it, mail not yet sent for it, the audio and Vexa's copy | the **recorded hours stay counted** (`owner_usage_seconds` 1800 before and after), the AI-run cost rows, the calendar join record |

## 5. UX consequences the frontend must surface

- **Deleting does not give hours back.** The confirmation should not imply it does.
- **Cancel first.** While the bot is in the call or the notes are being written the answer is 409
  `meeting_active`; hide or disable the control for `can_cancel`/`can_end` meetings, or show the
  message as is. A cancelled meeting is `failed` and can be deleted (#17, #18).
- **A double click must not show an error**: the second call is 404 (#15). Treat 404 after a delete
  as success and drop the row.
- **502 `recording_delete_failed` means nothing was deleted** (#11): offer to try again.
- **Tasks:** the spec wants the confirmation to name how many tasks go too. Tasks arrive with W2; this
  endpoint returns no count (204). W2 adds the number to the meeting detail for the dialog.
- The page asks inline (the Decisions "Remove from log" ask, red `Delete meeting` button), then goes to
  the meetings list and says "Meeting deleted" in the toast. A 409 or 502 shows its own text and the
  page stays; a 404 is treated as deleted.

## 6. Designer gaps

The control and the ask are **lead-designed, not in the designer's file**: a red text button below the
meeting (as "Delete template"), the inline ask as "Remove from log", the toast as the designer's. The
designer should confirm or redraw them, and the wording that will name the task count once W2 lands.

## 7. Verification table

| Behaviour | Verified |
|---|---|
| 204 delete, rows gone, hours kept | live (#12, #13) |
| 409 `meeting_active` for `bot_sent` and `processing` | live (#08, #09) |
| 502 with nothing deleted | live, with a fake Vexa told to fail (#10, #11) |
| 404 for a repeat delete and for another account | live (#15, #21) |
| 403 without CSRF | live (#07) |
| `can_delete` true / false | live (#05, #06) |
| The screen (button, ask, toast) | vitest with captured bodies; shots in `docs/ui/parity/meeting-delete/` against the mock API, not the live backend |
| 409 `meeting_busy` | not live; see section 3 |
| Teammate on a shared meeting gets 404 | tests (`tests/test_team_visibility.py`), not in these captures |
| A real Vexa recording removed | not live: Vexa is a fake here |
