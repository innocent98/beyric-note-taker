# Frontend integration guide: Delete a meeting

Everything below is copied from responses captured against the running app by
`scripts/capture_meeting_delete.py` (real app under uvicorn, real migrations, real Postgres, real
HTTP; files in `docs/fe-captures/meeting-delete/NN-name.json`, cited as **#NN**). Run the script
again to re-derive any claim. Read it with `docs/fe-integration-guide-owner-app.md` (session cookie,
CSRF, error shape).

## 1. What it is, in the frontend's terms

One endpoint removes a meeting for good, with everything taken from it. **There is no screen for it
yet**: the designer's prototype draws "Delete template", "Delete project" and "Delete task" but no
"Delete meeting" and no confirmation (designer gap, see section 5). Nothing calls this endpoint
until that is drawn.

## 2. The endpoint

`DELETE /api/meetings/{id}`, owner only, `X-CSRF-Token` required like every write.

| Case | Status | Body | Capture |
|---|---|---|---|
| Deleted | 204 | none | #10 |
| No CSRF token | 403 | `{"error":{"code":"csrf_rejected","message":"Missing or wrong X-CSRF-Token header. Read it from GET /api/session."}}` | #05 |
| Bot in the call (`bot_sent`) | 409 | `{"error":{"code":"meeting_active","message":"This meeting is still being recorded or its notes are being written. Delete it once it has finished."}}` | #06 |
| Notes being written (`processing`) | 409 | the same `meeting_active` | #07 |
| Vexa would not remove the recording | 502 | `{"error":{"code":"recording_delete_failed","message":"The recording could not be removed, so nothing was deleted. Please try again in a moment."}}` | #08 |
| Already deleted, never existed, or not yours (a teammate or anyone else) | 404 | `{"error":{"code":"not_found","message":"No such meeting"}}` | #13, #19 |
| Worker holds the row | 409 `meeting_busy` | same shape as the other 409s; try again in a moment | **not captured live** (needs a race; shape from `_lock_meeting`, covered by `tests/test_end_meeting.py` for cancel) |

After a 204: `GET /api/meetings/{id}` is 404 (#12), the id is gone from `GET /api/meetings` (#14).

## 3. What goes, what stays

Row counts read from the tables before and after (#04, #09, #11):

| Gone with the meeting | Stays (no link to the meeting) |
|---|---|
| the meeting with its transcript and notes, translations, decisions, open questions, review items ("Please check"), notifications about it, mail not yet sent for it, the audio and Vexa's copy | the **recorded hours stay counted** (`owner_usage_seconds` 1800 before and after), the AI-run cost rows, the calendar join record |

## 4. UX consequences the frontend must surface

- **Deleting does not give hours back.** The confirmation should not imply it does.
- **Cancel first.** While the bot is in the call or the notes are being written the answer is 409
  `meeting_active`; hide or disable the control for `can_cancel`/`can_end` meetings, or show the
  message as is. A cancelled meeting is `failed` and can be deleted (#15, #16).
- **A double click must not show an error**: the second call is 404 (#13). Treat 404 after a delete
  as success and drop the row.
- **502 `recording_delete_failed` means nothing was deleted** (#09): offer to try again.
- **Tasks:** the spec wants the confirmation to name how many tasks go too. Tasks arrive with W2; this
  endpoint returns no count (204). W2 adds the number to the meeting detail for the dialog.
- Hide the control from a teammate reading a shared meeting: it answers 404 for them. There is no
  `can_delete` flag yet; use `GET /api/meetings/{id}` ownership once W3 exposes it.

## 5. Designer gaps

The delete control on the meeting page and the confirmation dialog (wording, the task count). Until
then the landing line "Delete a meeting" and the privacy page are true of the service (an owner can
have it done) but not of any screen.

## 6. Verification table

| Behaviour | Verified |
|---|---|
| 204 delete, rows gone, hours kept | live (#10, #11) |
| 409 `meeting_active` for `bot_sent` and `processing` | live (#06, #07) |
| 502 with nothing deleted | live, with a fake Vexa told to fail (#08, #09) |
| 404 for a repeat delete and for another account | live (#13, #19) |
| 403 without CSRF | live (#05) |
| 409 `meeting_busy` | not live; see section 2 |
| Teammate on a shared meeting gets 404 | tests (`tests/test_team_visibility.py`), not in these captures |
| A real Vexa recording removed | not live: Vexa is a fake here |
