# SOP: `TRANSCRIPT_SOURCE=skribby` made ready for `elevenlabs/scribe-v2` (one transcript)

**What shipped.** Three changes that make the Skribby-as-the-transcript mode ready to run with ElevenLabs Scribe v2: (1) the only model allowed with `TRANSCRIPT_SOURCE=skribby` is now `elevenlabs/scribe-v2`; (2) in that mode the 7-day speaker evidence is now kept (it was silently not kept); (3) `custom_vocabulary` is sent only to models Skribby lists with it. **Branch** `feature/skribby-elevenlabs-single-transcript`; PR number in the PR list (opened 2026-11-13, not merged by this work). No migration, no endpoint or response shape (no FE guide change, `docs/openapi.json` unchanged). Nothing is switched on: the server `.env` and the `skribby_transcription_model` default are untouched. The sibling PR `feature/skribby-vocabulary-pass` (stacked on this one) adds the vocabulary post-pass, see `docs/sop/2026-11-13-skribby-vocabulary-pass.md`.

## Why

Adebayo decided (2026-11-13) to move to ONE transcript, Skribby's, if it holds up: about $0.27 less per meeting hour and no alignment step (`notes.label_transcript`) laying voice tags over our text. Evidence so far: `elevenlabs/scribe-v2` won the model bake-off on voice separation (`docs/sop/2026-11-11-skribby-model-bakeoff.md`), and on two 10-minute windows of meeting 34 its text was more complete than ours (ours had 37 and 23 dropped passages marked with an ellipsis, ElevenLabs 1 and 2) and spelled product names more consistently, though no model got every name right. `soniox/stt-async-v5` is marked deprecated on Skribby and nothing uses it.

## How

| Change | What and why |
|---|---|
| Allowlist | `TRANSCRIPT_SKRIBBY_MODELS` in `app/config.py` is `("elevenlabs/scribe-v2",)`. The startup refusal (`Settings._skribby_transcript_needs_a_diarizing_model`) therefore names it, and refuses v5 and the Groq default. The refusal only applies when `TRANSCRIPT_SOURCE=skribby`; with `own` any model still starts. The `skribby_transcription_model` default stays `groq/whisper-large-v3-turbo`, so **turning the source on needs `SKRIBBY_TRANSCRIPTION_MODEL=elevenlabs/scribe-v2` too** (the server already has it) |
| Evidence in skribby mode | The evidence (`skribby_segment_evidence`: items, tags, by_elimination, and the Meet speaker `events`) was saved only in the speakers step, which in this mode does not fetch Skribby again (the names were saved by the transcribing step), so **no evidence was kept** (the old follow-up). `_skribby_segments` now also returns the `evidence` of the one fetch the transcribing step makes, and when Skribby's transcript is used that step calls the same `_keep_evidence(session, meeting_id, evidence, events)` with the events fetch (`_speaker_events`), in the same commit that saves the transcript. Same row, same 7-day retention (`delete_expired_audio`), same savepoint (a failing write rolls back only itself and the transcript stays), same "never stops the meeting", same "exception class only in the log, never text". On a **fallback** to our own text the transcribing step keeps nothing and the speakers step does, as before, so there is never a second row. `own` mode is unchanged |
| Vocabulary sending | `app/skribby.py::VOCABULARY_MODELS` = `soniox/stt-async-v6`, `groq/whisper-large-v3-turbo`, `groq/whisper-large-v3`, `mistral/voxtral-mini-2`, `gladia/async`; `takes_vocabulary(model)`. `SkribbyClient.payload` adds `custom_vocabulary` only for those, and `transcript_source.send_options` does not even read the owner's terms for another model. The HTTP 400/422 retry without the field is unchanged |

**Where the model list comes from.** Read on 2026-11-13: `skribby.io/pricing` (the "Custom Vocab" capability tag) and `skribby.io/docs/guides/billing` (the "custom vocab" feature column), ids from `skribby.io/docs/guides/transcription-models`. Both pages list Groq Whisper Large v3 Turbo, Groq Whisper Large v3, Soniox v6, Mistral Voxtral Mini 2 and Gladia. They **disagree** on Deepgram (pricing lists Nova-2 and Nova-3 with it, billing does not), AssemblyAI (pricing lists Universal, billing does not) and Speechmatics (billing lists it, pricing does not), so those are left out. **Neither page lists `elevenlabs/scribe-v2`** (the model Skribby then sends nothing: `custom_vocabulary` is documented as "only supported on certain models"). `soniox/latest`, Soniox v5 (deprecated) and the realtime models are left out too.

## What's involved

`app/config.py` (allowlist and comment), `app/pipeline.py` (`_skribby_segments`, `_step_transcribing`), `app/skribby.py` (`VOCABULARY_MODELS`, `takes_vocabulary`, `SkribbyClient.payload`), `app/transcript_source.py` (`send_options`), `.env.example`, `deploy/README.md`, `docs/sop/2026-11-08-skribby-provider.md`, `-09-transcript-source-switch.md`, `-10-skribby-raw-segments.md` (v5 and the follow-up), `docs/checklist/master-checklist.md`. Tests: `tests/test_transcript_source.py`, `tests/test_skribby_evidence.py`.

## Verification

Run on the Mac, bounded: `ruff check .`, `ruff format --check .` (pinned 0.17.3), and `tests/test_transcript_source.py`, `tests/test_skribby_evidence.py`, `tests/test_skribby_provider.py`, `tests/test_pipeline.py`, `tests/test_placement.py`, `tests/test_skribby_model_bakeoff.py` (all pass; a scratch Postgres, removed afterwards). New tests: skribby mode keeps exactly one evidence row with items, tags and events, and asks for the events once; the row has the same 7-day retention; no evidence means no row and no events call; a write that cannot be saved leaves the meeting `ready` with its transcript and `transcript_source = skribby` (no text in the log); failing events cost only the events; a fallback keeps one row from the speakers step; `own` mode keeps one row as before; the startup refusal names ElevenLabs and refuses v5; the listed models get `custom_vocabulary`, ElevenLabs, Deepgram, AssemblyAI, Speechmatics, v5 and `none` do not; `send_options` returns nothing for ElevenLabs. CI runs everything else.

**Not run live.** Nothing was run against the real Skribby or ElevenLabs. Unproven: that Skribby really does not accept `custom_vocabulary` for ElevenLabs (we simply do not send it), the pages' listing may change, and a real meeting's evidence row in skribby mode (the events shape was proven only for the speakers-step path).

## Operate / roll back

No server steps before merge (no migration, no `.env` change). **To turn the single transcript on later** (lead and Adebayo, a window with no meeting running): server `.env` `TRANSCRIPT_SOURCE=skribby`, keep `SKRIBBY_TRANSCRIPTION_MODEL=elevenlabs/scribe-v2` and `BOT_PROVIDER=skribby`, and `TRANSCRIPT_SHADOW_PERCENT=25` to keep a sampled comparison with our transcription (0 to stop it; it costs $0.70 an hour of the sampled meetings); restart web, worker and scheduler. Flip back: `TRANSCRIPT_SOURCE=own`, restart the same three. Revert the code: revert the PR.

## Follow-ups

- The owner's vocabulary cannot reach ElevenLabs: the stacked vocabulary-pass PR is the workaround.
- Re-read the two Skribby pages before the flip: the model list is a snapshot of 2026-11-13.
- Compare real meetings with the shadow report before dropping our own transcription for good.
