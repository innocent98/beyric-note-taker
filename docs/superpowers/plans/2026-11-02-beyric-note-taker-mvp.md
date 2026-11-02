# Beyric Note Taker MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paste a Meet or Zoom link, and get an accurate transcript and trustworthy notes after the meeting.

**Architecture:** A FastAPI web app and one worker process share Postgres. Vexa Lite joins and records. The worker polls Vexa, prepares the audio, calls OpenAI, and stores the results.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLAlchemy 2, Alembic, Postgres 17, httpx, openai SDK, ffmpeg, pytest, ruff, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-02-beyric-note-taker-design.md`

## Global Constraints

- Bot name: `Beyric Note Taker (recording)`.
- Transcription model: `gpt-transcribe`. Notes and speaker model: `gpt-5.4-mini`. Both set in configuration.
- Audio: mono, 32 kbps Opus, chunks of at most 15 minutes, each under 25 MB.
- Poll Vexa every 30 seconds. Delete audio after 7 days.
- One bot at a time.
- UI must not say "recording" from Vexa status alone. Use "Bot sent".
- Notes must not state anything unsupported by the transcript.
- Commits and PRs carry no AI attribution. Branches never start with `claude/`.
- Update the SOP and master checklist as each task lands.

## Review Focus

1. **Bot never admitted, or removed:** the meeting shows failed with a plain reason, and no OpenAI cost is incurred.
2. **Worker restarts mid-job:** the meeting resumes and finishes with no duplicate notes.
3. **One chunk fails at OpenAI:** it retries, then fails clearly, keeping the audio.
4. **Vexa unreachable while polling:** the meeting stays in progress and recovers, up to a 6-hour limit.
5. **Empty or near-silent meeting:** the app says no speech was found and writes no invented notes.

---

### Task 0: Vexa check and project setup

**Files:**
- Create: `pyproject.toml`, `Dockerfile`, `compose.yaml`, `.env.example`, `.github/workflows/ci.yml`
- Create: `app/config.py`, `app/db.py`, `app/models.py`, `alembic/`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `Settings`, `get_session()`, models `Meeting`, `Template`, `VocabularyTerm`
- `Meeting.status`: `bot_sent | processing | ready | failed`
- `Meeting.step`: `waiting | audio | transcribing | speakers | notes | done`

- [ ] **Step 1: Run the live check with Adebayo**

Start Vexa Lite. Send a bot with `transcribe_enabled: false` to a Google Meet and a Zoom call with two display names.
Expected: `GET /transcripts/{platform}/{id}` still returns named speakers, or the result shows they depend on live transcription.

- [ ] **Step 2: Record the result in the spike report and set `VEXA_LIVE_TRANSCRIBE`**

- [ ] **Step 3: Write the failing test**

```python
def test_new_meeting_defaults(session):
    m = Meeting(url="https://meet.google.com/abc-defg-hij", platform="google_meet",
                native_id="abc-defg-hij", template_id=1)
    session.add(m); session.commit()
    assert m.status == "bot_sent"
    assert m.step == "waiting"
    assert m.failure_reason is None
```

- [ ] **Step 4: Run it**

Run: `pytest tests/test_models.py -v`
Expected: FAIL, `Meeting` not defined.

- [ ] **Step 5: Implement settings, models and the first migration**

`Meeting` also holds `vexa_meeting_id`, `transcript`, `speaker_transcript`, `notes`, `started_polling_at`, `audio_deleted_at`, `created_at`. `Template` holds `name` and `instructions`. Seed General, Client call, Team update and Interview in the migration.

- [ ] **Step 6: Run tests, `ruff check .`, and a migration upgrade and downgrade on a scratch database**

Expected: all pass, one Alembic head.

- [ ] **Step 7: Add CI running ruff and pytest, then commit**

```bash
git commit -m "chore: set up project and data models"
```

### Task 1: Vexa client

**Files:**
- Create: `app/vexa.py`
- Test: `tests/test_vexa.py`, `tests/fixtures/vexa_transcript.json` (synthetic names only)

**Interfaces:**
- Produces: `parse_meeting_url(url) -> tuple[str, str]`
- Produces: `VexaClient.send_bot(url) -> int`
- Produces: `VexaClient.get_meeting(platform, native_id) -> VexaMeeting`
- Produces: `VexaClient.download_audio(recording_id, media_id, path) -> None`
- `VexaMeeting`: `status`, `segments`, `recording_id`, `media_id`, `recording_status`

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_zoom_url():
    assert parse_meeting_url("https://us05web.zoom.us/j/123456789?pwd=abc") == ("zoom", "123456789")

def test_parse_meet_url():
    assert parse_meeting_url("https://meet.google.com/abc-defg-hij") == ("google_meet", "abc-defg-hij")

def test_rejects_other_urls():
    with pytest.raises(UnsupportedMeetingUrl):
        parse_meeting_url("https://example.com/meeting")

def test_download_uses_raw_endpoint(httpx_mock, tmp_path):
    ...  # assert request path is /recordings/1/media/2/raw?type=audio, not /master
```

- [ ] **Step 2: Run them**

Run: `pytest tests/test_vexa.py -v`
Expected: FAIL, functions not defined.

- [ ] **Step 3: Implement using `X-API-Key`, `POST /bots`, `GET /transcripts/{platform}/{id}` and the raw recording endpoint**

Pass the full `meeting_url` so the Zoom `pwd` is preserved.

- [ ] **Step 4: Run tests**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: add Vexa client"
```

### Task 2: Audio preparation

**Files:**
- Create: `app/audio.py`
- Test: `tests/test_audio.py`

**Interfaces:**
- Produces: `prepare_chunks(source: Path, out_dir: Path) -> list[Path]`
- Raises: `NoSpeechError` when the audio has no usable sound

- [ ] **Step 1: Write the failing tests**

Generate test audio with `ffmpeg` tones.

```python
def test_one_hour_audio_splits_under_limits(long_audio, tmp_path):
    chunks = prepare_chunks(long_audio, tmp_path)
    assert len(chunks) >= 4
    assert all(p.stat().st_size < 25_000_000 for p in chunks)
    assert all(duration(p) <= 15 * 60 for p in chunks)

def test_silent_audio_raises(silent_audio, tmp_path):
    with pytest.raises(NoSpeechError):
        prepare_chunks(silent_audio, tmp_path)
```

- [ ] **Step 2: Run them**

Run: `pytest tests/test_audio.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement with `ffmpeg`, cutting at the nearest silence within the last 30 seconds of each 15-minute window**

Cut at the 15-minute mark if there is no silence. Use `volumedetect` for the silent check.

- [ ] **Step 4: Run tests and commit**

```bash
git commit -m "feat: prepare meeting audio for transcription"
```

### Task 3: Transcription with custom vocabulary

**Files:**
- Create: `app/transcribe.py`
- Test: `tests/test_transcribe.py`

**Interfaces:**
- Consumes: `prepare_chunks` output
- Produces: `transcribe_chunks(chunks: list[Path], terms: list[str]) -> str`

- [ ] **Step 1: Run one live call to confirm how `keywords` must be sent**

Transcribe a short clip saying "Beyric" with and without keywords.
Expected: the keyword call spells "Beyric" correctly. If not, use `prompt` and record that.

- [ ] **Step 2: Write the failing tests with a fake OpenAI client**

```python
def test_joins_chunks_in_order(fake_openai): ...
def test_retries_then_succeeds(fake_openai): ...      # fails twice, then succeeds
def test_gives_up_after_three_attempts(fake_openai):
    with pytest.raises(TranscriptionFailed): ...
def test_invalid_term_is_removed_from_keywords(fake_openai): ...  # term containing < or >
```

- [ ] **Step 3: Run them**

Run: `pytest tests/test_transcribe.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement, using `gpt-transcribe` and the method confirmed in Step 1**

Removing invalid terms matters because the API rejects the whole request otherwise.

- [ ] **Step 5: Run tests and commit**

```bash
git commit -m "feat: transcribe recordings with custom vocabulary"
```

### Task 4: Speakers and notes

**Files:**
- Create: `app/notes.py`, `tests/eval/sample_transcript.txt` (synthetic meeting)
- Test: `tests/test_notes.py`

**Interfaces:**
- Produces: `label_speakers(transcript: str, segments: list[dict]) -> str`
- Produces: `generate_notes(transcript: str, template: Template, terms: list[str]) -> str`

- [ ] **Step 1: Write the failing tests with a fake OpenAI client**

```python
def test_notes_prompt_treats_transcript_as_source_of_truth(fake_openai): ...
def test_notes_prompt_includes_template_and_vocabulary(fake_openai): ...
def test_notes_output_has_required_headings(fake_openai):
    # Summary, Key points, Decisions, Action items, Open questions, Please check
    ...
def test_label_speakers_keeps_transcript_when_no_segments(): ...
def test_label_speakers_falls_back_if_words_change(fake_openai): ...
```

The last test compares the words in input and output. If they differ by more than 2%, return the unlabelled transcript, so speaker labelling can never rewrite what was said.

- [ ] **Step 2: Run them**

Run: `pytest tests/test_notes.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement with the spike prompt, plus a "Please check" section for unclear or doubtful items**

- [ ] **Step 4: Run tests**

Expected: PASS.

- [ ] **Step 5: Run a live evaluation on the synthetic transcript and review the notes by hand**

Expected: no invented facts, and action items keep their owners.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat: generate speaker-labelled transcripts and notes"
```

### Task 5: Worker pipeline

**Files:**
- Create: `app/pipeline.py`, `app/worker.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: Tasks 1-4
- Produces: `advance_meeting(session, meeting_id) -> None`
- Produces: `delete_expired_audio(session, now) -> int`

- [ ] **Step 1: Write the failing tests**

```python
def test_completed_meeting_reaches_ready(...): ...
def test_failed_vexa_meeting_sets_reason_and_makes_no_openai_call(...): ...
def test_completed_without_recording_fails_with_admission_reason(...): ...
def test_resume_after_restart_does_not_repeat_finished_steps(...): ...
def test_vexa_outage_keeps_bot_sent_status(...): ...
def test_gives_up_after_six_hours(...): ...
def test_no_speech_sets_clear_reason(...): ...
def test_audio_deleted_after_seven_days(...): ...
```

- [ ] **Step 2: Run them**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement one step at a time, saving `Meeting.step` after each**

The worker loop takes one meeting per pass using `SELECT ... FOR UPDATE SKIP LOCKED`. Saved results are reused, so a restart costs no second transcription.

- [ ] **Step 4: Run tests and commit**

```bash
git commit -m "feat: add meeting processing worker"
```

### Task 6: Web pages

**Files:**
- Create: `app/main.py`, `app/auth.py`, `app/web.py`, `app/templates/*.html`, `app/static/app.css`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `parse_meeting_url`, `VexaClient.send_bot`, models
- Produces routes: `GET/POST /login`, `GET /`, `POST /meetings`, `GET /meetings/{id}`, `POST /meetings/{id}/regenerate`, `GET/POST /templates`, `GET/POST /vocabulary`

- [ ] **Step 1: Write the failing tests**

```python
def test_pages_require_login(client): ...
def test_paste_link_sends_bot(client, fake_vexa): ...
def test_second_active_meeting_is_refused(client, fake_vexa): ...
def test_invalid_link_shows_plain_error(client): ...
def test_status_text_says_bot_sent_not_recording(client): ...
def test_regenerate_makes_no_transcription_call(client, fake_openai): ...
def test_failed_meeting_shows_reason(client): ...
```

- [ ] **Step 2: Run them**

Run: `pytest tests/test_web.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement pages, with copy buttons for notes and transcript**

Refresh the meeting page every 30 seconds until it is `ready` or `failed`. Layouts must work at 375 px wide.

- [ ] **Step 4: Run tests, check the pages at phone width in the browser, then commit**

```bash
git commit -m "feat: add meeting web pages"
```

### Task 7: Deploy

**Files:**
- Create: `deploy/nginx.conf`, `deploy/README.md`, `deploy/backup.sh`
- Modify: `compose.yaml`

- [ ] **Step 1: Adebayo provides an Ubuntu 24.04 server and a domain**

Minimum 4 GB of memory and 2 vCPUs.

- [ ] **Step 2: Set the provider firewall to allow only 22, 80 and 443**

Verify from outside.
Expected: ports 8056, 3001 and 8100 do not respond.

- [ ] **Step 3: Confirm `~/.claude/.credentials.json` is absent, then start Vexa Lite with `make -C deploy/lite up`**

- [ ] **Step 4: Start the app and worker, then enable HTTPS and a daily Postgres backup**

- [ ] **Step 5: Restore a backup to a scratch database**

Expected: meetings and notes are present.

- [ ] **Step 6: Commit**

```bash
git commit -m "chore: add deployment setup"
```

### Task 8: Real-world acceptance

**Files:**
- Modify: `docs/spike/vexa-spike-report.md`, `docs/checklist/master-checklist.md`
- Create: `docs/sop/2026-11-02-note-taker-mvp.md`

- [ ] **Step 1: Run a Google Meet of an hour or more through the deployed app**

Expected: `ready`, no crash, memory stays below the server limit.

- [ ] **Step 2: Run a Zoom call hosted by another account, with a waiting room**

Use a paid Zoom account if one is available.
Expected: `ready`, with distinct speaker names.

- [ ] **Step 3: Adebayo rates the notes and transcript against his current tool**

Expected: he rates them better and spots no invented facts. Log any corrections.

- [ ] **Step 4: Record actual OpenAI spend per meeting hour**

Expected: about $0.30–$0.70. If it's higher, find out why before regular use.

- [ ] **Step 5: Update the SOP and checklist, then commit**

```bash
git commit -m "docs: record MVP acceptance results"
```
