# Frontend integration guide: download and export (AI workspace phase W5)

**Status:** written 2026-11-03 from branch `feature/notes-export-w5`. Every status code, header, body and error string below is copied from a response the **running app** gave over real HTTP, saved by `scripts/capture_export_api.py` under `docs/fe-captures/export/` (re-run it to re-derive any claim). Two failures cannot be provoked from outside and were provoked inside the capturing process; they are marked **PROVOKED**. Nothing here was written from the schema or the code.

Placeholders: meeting ids (`1`), the title `Google Meet abc-defg-hij`, the people `Ada Example` and `Ben Sample`, and the account `owner@example.test` are fake. The capture masks CSRF tokens as `<csrf-A>`.

Machine-readable schema: `docs/openapi.json` (the route is `export_meeting`). The generated type of the `format` query is `ExportFormat` in `web/lib/api/types.ts`.

---

## 1. What this is, in the user's terms

On the meeting page the owner gets a **Download** button with two choices, **PDF** ("Best for reading and printing") and **Word document** ("Edit in Word or Google Docs"), and a tick **"Include the full transcript"**. Choosing a format downloads a file of the notes as the notes tab shows them: all six sections, in order.

One endpoint serves it. **The file is built when asked and is never stored**: there is no file id, no link to share, nothing to poll. The same click twice builds it twice.

| What the user does | Call |
|---|---|
| Download the notes as PDF | `GET /api/meetings/{id}/export?format=pdf` (or no `format`: PDF is the default) |
| Download them as Word | `GET /api/meetings/{id}/export?format=docx` |
| Tick "Include the full transcript" | add `&transcript=true` (default `false`) |

Not part of this phase (so not in the API yet): translated downloads (`language=`), Share by email, the "In French, with summary..." note under the tick. Do not draw them.

## 2. The endpoint

```http
GET /api/meetings/{meeting_id}/export?format=pdf|docx&transcript=true|false
```

- Needs a login (the session cookie). **No CSRF token**: it is a GET. A logged-out call is a 401 (4.1).
- `meeting_id` is the same id as `GET /api/meetings/{id}`. **Another account's meeting is a 404, never a 403**, exactly like every other meeting route.
- `format`: `pdf` or `docx`. Anything else is a 422 (4.6). Default `pdf`.
- `transcript`: `true` or `false`. Anything else is a 422 (4.7). Default `false`.
- The meeting must be **ready**: status `ready` with notes. Otherwise 409 `not_ready` (4.3).

### 2.1 Success: PDF without the transcript

Request: `GET /api/meetings/1/export` (no query at all).

```http
HTTP 200
content-type: application/pdf
content-disposition: attachment; filename="Google Meet abc-defg-hij.pdf"; filename*=UTF-8''Google%20Meet%20abc-defg-hij.pdf
content-length: 14942
cache-control: no-store
x-content-type-options: nosniff
```
The body is the binary file (first bytes `%PDF-1.7`). _Capture: `05-export-pdf-default.json`; the file itself is `files/05-export-pdf-default.pdf`._

### 2.2 Success: the other three

| Request | Content-Type | Capture |
|---|---|---|
| `?format=pdf&transcript=true` | `application/pdf` | `06-export-pdf-transcript.json` |
| `?format=docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | `07-export-docx.json` |
| `?format=docx&transcript=true` | the same Word type | `08-export-docx-transcript.json` |

All four answer 200 with the same `content-disposition` pattern: `attachment; filename="<title>.<ext>"; filename*=UTF-8''<percent-encoded title>.<ext>`. Sample files for all of them are in `docs/fe-captures/export/files/` (placeholder text only; open them in Word and a PDF viewer to see what the user gets).

A meeting that has **no speaker-labelled transcript** (`speaker_transcript` empty) exports its plain transcript instead, as paragraphs without speaker names (`09-export-docx-transcript-unlabelled.json`). The notes tab's `transcript` field in `GET /api/meetings/{id}` is the same text (`speaker_transcript or transcript`), so **show the tick only when that field is non-empty**; a meeting with no transcript at all exports the notes alone even if the tick is sent.

### 2.3 The file name

The server names the file from the meeting's title (the `title` of `GET /api/meetings/{id}`): characters that cannot go in a file name (`/ \ : * ? " < > |`), control characters and line breaks are removed, runs of spaces become one, at most 100 characters, and the extension is `.pdf` or `.docx`. An empty result becomes `meeting-notes`. **Use the name from the header**: prefer `filename*` (UTF-8, percent-encoded) and fall back to `filename`. The `filename=` value is an ASCII fallback (accents stripped), so it can differ from `filename*` for a title like "Réunion équipe". (The sanitising is unit-tested; today every captured meeting's title is `Google Meet abc-defg-hij`, so no capture shows an accented or cleaned name: **not verified live**.)

## 3. How to download it in the browser (what `web/lib/api/client.ts` does)

A plain link (`<a href="/api/meetings/1/export">`) would send the visitor to a JSON error page on any failure, and could not show "preparing". So:

1. `fetch` the URL with the session cookie (`api.meetings.export(id, format, transcript)`).
2. On a non-2xx answer, read the usual JSON error (section 4) and show its `message`.
3. On 200, take the blob, make an object URL, click a hidden `<a download="<name>">`, and revoke the URL.

Through the Next front door the bytes, `content-disposition` and `retry-after` pass unchanged (checked in a browser through `next dev` proxying FastAPI: a 200 PDF of 231,974 bytes starting `%PDF-`, and a 503 with `retry-after: 5`). The page's Content-Security-Policy needed no change (`connect-src 'self'` covers the fetch; a blob download is not a navigation).

## 4. Every error the FE must handle

All use the usual shape `{"error": {"code", "message"}}` (see the owner-app guide, section 4). Switch on `code`; `message` is written to be shown as is.

| # | Status | `code` | When | Capture |
|---|---|---|---|---|
| 4.1 | 401 | `not_authenticated` | no login | `02-export-not-logged-in.json` |
| 4.2 | 404 | `not_found` | unknown id, or another account's meeting | `12-...`, `13-...` |
| 4.3 | 409 | `not_ready` | the meeting is not `ready` (still recording or writing, or failed) | `10-...`, `11-...` |
| 4.4 | 503 | `export_busy` | another file is being built in this server process | `16-export-busy.json` |
| 4.5a | 504 | `export_timeout` | the build took longer than 45 s and was stopped (**PROVOKED**) | `17-export-timeout.json` |
| 4.5b | 500 | `export_failed` | the renderer failed (**PROVOKED**) | `18-export-failed.json` |
| 4.6 | 422 | `validation_error` | unknown `format` | `14-...` |
| 4.7 | 422 | `validation_error` | `transcript` is not a boolean | `15-...` |

Bodies, verbatim:

```json
{"error": {"code": "not_authenticated", "message": "Log in first."}}
```
(401)
```json
{"error": {"code": "not_found", "message": "No such meeting"}}
```
(404; the message is developer text, `ApiError.isShowable` is false for 404, show your own "This meeting is not available")
```json
{"error": {"code": "not_ready", "message": "The notes are not ready yet."}}
```
(409, a processing meeting and a failed one give the same body)
```json
{"error": {"code": "export_busy", "message": "Another file is being prepared. Try again in a few seconds."}}
```
(503, with the response header `retry-after: 5`)
```json
{"error": {"code": "export_timeout", "message": "Preparing the file took too long. Try again, or leave the transcript out."}}
```
(504)
```json
{"error": {"code": "export_failed", "message": "The file could not be prepared. Try again."}}
```
(500; says nothing of the cause on purpose)
```json
{"error": {"code": "validation_error", "message": "Some fields are missing or not valid.", "fields": [{"field": "format", "message": "Input should be 'pdf' or 'docx'"}]}}
```
(422, 4.6; for 4.7 the field is `transcript` and the message is `Input should be a valid boolean, unable to interpret input`)

**PROVOKED means:** the time limit was set to 0.2 s for that single call (it is 45 s in the app) so the real child process was really killed, and the renderer's PDF library was made unloadable (what a missing Pango looks like) so the real child really failed. The answers are the app's real answers; whether production ever produces them is the part that was induced.

### Field-nesting and value traps
- Success is **not JSON**: do not call `.json()` on a 200. Check `res.ok` first; on a failure the body is JSON even though the success is binary.
- `GET /api/meetings/{id}` is the only place the title and the notes live; the export response carries neither, only the file and its name in the header.
- A meeting that is `ready` but whose notes are empty also answers 409 `not_ready` (unit-tested, not captured: a real meeting never reaches `ready` without notes).

## 5. States and what the screen does

| State | Trigger | What to show | Verified |
|---|---|---|---|
| Not ready | meeting status `bot_sent` or `processing` | the **Download button disabled**, with the reason ("Available when the notes are ready.") for screen readers | yes, in a browser (processing meeting) |
| No notes ever | meeting status `failed` | **no Download control** | unit test |
| Idle | `ready` | button with the menu: PDF, Word, the transcript tick (only when the meeting has a transcript) | yes, in a browser, desktop and phone width |
| Working | after the click, until the response | the button reads "Preparing PDF..." (or "Word document") and is disabled, plus a line "Preparing PDF... a long meeting can take a few seconds." No polling: one request | yes, in a browser |
| Done | 200 | the browser saves the file; a line "`<file name>` downloaded" for 8 s | yes, in a browser (the line); that the browser **wrote the file to disk** was not observable in the embedded browser |
| Busy | 503 `export_busy` | show the message; the user clicks again. Do not retry by itself in a loop | yes (the request, the message by unit test) |
| Too slow | 504 `export_timeout` | show the message; the user may untick the transcript or choose Word | message by unit test; the 504 is **PROVOKED** |
| Failed | 500 `export_failed`, or the network | show the message | message by unit test; the 500 is **PROVOKED** |
| Stale page | 409 `not_ready` | show the message **and refetch the meeting** so the page follows the server | unit test |

**Cadence:** none. A build takes about 1 to 2 s for a one-hour meeting and up to about 9 s for 40,000 words on a loaded test machine (`docs/spike/export-pdf-memory.md`); the 45 s limit is the server's, and nginx's 60 s read timeout is above it.

## 6. UX consequences the FE must surface

- **The first click can be slower than the next** (a few seconds after a server restart). The "Preparing..." line is not optional.
- **One file at a time per server.** If two people click at once, the second gets `export_busy`. Show the server's message; do not hide it, do not retry silently.
- **The transcript makes PDFs much longer** (a 1-hour meeting is about 21 pages, 3 hours about 59). The tick is the user's choice, off by default; do not remember it as on.
- **Nothing is stored.** Do not offer "download again later" or a link; the privacy policy says exports are built when asked and not stored.
- **The file is a snapshot.** If the notes are rewritten after, an earlier download does not change. Nothing to do; do not imply otherwise.

## 7. Verification table

| Behaviour | How | Status |
|---|---|---|
| PDF and Word download, with and without transcript, names and types (2.1, 2.2) | real app over HTTP, in the app's Docker image | **verified live** |
| Plain transcript used when there are no speaker labels (2.2) | same | verified live |
| 401, 404 (another account's meeting, unknown id), 409 (processing, failed), 422 (format, transcript), 503 busy | same; the 503 by sending a second request 0.6 s into a 40,000-word PDF | **verified live** |
| 504 `export_timeout` and 500 `export_failed` bodies | same, but each failure was provoked inside the capturing process | **verified live, failure provoked** |
| The files open and read correctly | PDF rendered to images with `pdftoppm` and read; Word read by `python-docx` and by macOS's Word reader (`textutil`, QuickLook) | **checked on those; not opened in Microsoft Word, Google Docs or Pages** (QuickLook ignores the page break before the transcript and flattens nested bullets; Word was not available) |
| Through the Next proxy: bytes, `content-disposition`, `retry-after` | `next dev` in front of the app, driven in the embedded browser | verified (development server, not the production build) |
| The menu, working and done lines, disabled state, phone width | embedded browser against `next dev` | verified |
| Failed and busy lines on screen, 409 refetch | unit tests that replay the captured error bodies (`web/tests/download-menu.test.tsx`) | verified in tests only, **not seen in a browser** |
| The browser saving the file to disk | not observable in the embedded browser | **not verified** |
| A filename with accents or characters to clean (2.3) | unit tests | not verified live (no such title exists in the captures) |
| Safari, Firefox, the live site, the production CSP | not tried | **not verified** |
| Timings on the production server | measured in an arm64 Docker VM under load (`docs/fe-captures/export/timings.json`) | indicative only |
