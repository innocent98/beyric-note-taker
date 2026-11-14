# SOP: Privacy Policy names the vendors that receive recordings (Skribby, ElevenLabs, OpenAI)

**What shipped.** The Privacy Policy now says who really receives meeting data. Branch `fix/privacy-subprocessors`; the PR is titled "Privacy Policy: name the vendors that receive recordings (do not merge before the lawyer reads it)". Only wording changed: no migration, no endpoint, no FE shape. The "Last updated" date is already 14 November 2026 and `TERMS_VERSION` / `RECORDING_ACK_VERSION` (`app/signup.py`) are untouched, as decided. No promise of notice of changes was added.

## Why

`privacy-content.tsx` (and `app/templates/privacy.html`) said: "The software that joins meetings runs on our own server; no other company receives the recording from it." That stopped being true on 2026-11-09, when the bot became Skribby, a hosted vendor (found by the read-only public pages audit, `docs/superpowers/specs/2026-11-14-public-pages-audit.md`, section 4). Three more sentences were wrong or missing for the same reason: the Security paragraph said the bot "runs on a separate network from our database"; the list named only OpenAI as the one that turns audio into a transcript; "our providers do not use them for training" had no source for Skribby's side.

## What each vendor receives today (from the code, then the vendor's own page)

| Vendor | Receives | Kept for | Source in our code | Vendor page read 2026-11-14 |
|---|---|---|---|---|
| Skribby (Alianza) | meeting link; the call's audio; participant display names and who spoke | we ask it to delete after we fetch the audio and save speaker names (`pipeline` speakers step, `deletion.delete_provider_copy` on meeting or account deletion); its default for recordings is one week; **debugging data incl. screenshots 30 days, whatever deletion request** | `app/skribby.py`, `docs/sop/2026-11-08-skribby-provider.md` sections 1.6 and 7 | skribby.io/privacy-policy (section 7), skribby.io/sub-processors, skribby.io/docs/guides/transcription-privacy, skribby.io/docs/guides/regions |
| ElevenLabs (via Skribby) | the call's audio, from Skribby, when `SKRIBBY_TRANSCRIPTION_MODEL=elevenlabs/scribe-v2` | Skribby's page says ElevenLabs has no zero-retention or deletion path, so its schedule | `app/config.py` (`skribby_transcription_model`), server flipped 2026-11-13 18:31 WAT per the lead's `printenv` check | skribby.io/docs/guides/transcription-privacy; ElevenLabs' own policy elevenlabs.io/privacy-policy |
| OpenAI | with `TRANSCRIPT_SOURCE=own` (today): the downloaded audio (`gpt-transcribe`); text of transcript and notes for notes, extraction, vocabulary spelling pass, translation, topics, Ask Beyric | its page: no training on API data; abuse-monitoring logs up to 30 days by default | `app/transcribe.py`, `notes.py`, `extraction.py`, `vocabulary_pass.py`, `translation.py`, `topics.py`, `ask.py` | developers.openai.com/api/docs/guides/your-data (platform.openai.com/docs/guides/your-data serves the same page) |
| Resend, Slack, Linear, Contabo, Cloudflare | unchanged, already listed | | | not re-read (not part of this task) |

## How, and what was left out

- The Skribby and ElevenLabs entries use Skribby's own words wherever the fact is Skribby's (one week, 30 days, no training, no ElevenLabs deletion path) and say "Skribby says". The policy does not assert what we have not seen. It does not name Skribby's account region: `SKRIBBY_REGION` defaults to `eu` in code but the server value was not checked; Skribby's own list says its bots can run in Europe, the United States or Japan "by meeting region", and the policy repeats only that.
- "We ask Skribby to delete" (not "we delete"): the live check showed `DELETE /bot` works (bot then 404) but the recording link answered 403, which is inconclusive (SOP above, section 1.6).
- The policy's OpenAI entry also lists translation, Ask Beyric and topics, which were missing from the old list.
- Rejected: naming Skribby's own sub-processors one by one. They change; the policy links Skribby's list instead.
- Both pages kept identical (`web/tests/legal.test.tsx` fails if they drift); `{days}` / `{{ audio_retention_days }}` unchanged.

## What's involved

`web/components/legal/privacy-content.tsx`, `app/templates/privacy.html` (the share list, the paragraph after it, the screenshot sentence under Meeting content, the training sentence under How we use, one new retention item, the Security sentence, the International transfers paragraph), `tests/test_legal.py` (new `test_the_policy_names_the_vendors_that_receive_the_recording_and_says_what_they_keep`), this SOP, `docs/checklist/master-checklist.md`.

## Verification

Locally (bounded, per the standing rule): `ruff check` and `ruff format --check` on `tests/test_legal.py`; web lint, format, type-check as CI runs them; `tests/test_legal.py` and `web/tests/legal.test.tsx`. CI runs the rest. Wording was checked against the saved vendor pages by reading them; the checked text is in the PR body. Not verified live: the server's actual Skribby region and transcription model today, and whether our OpenAI account has any zero-retention exception.

## Operate / roll back

Revert the PR. **If the server's `TRANSCRIPT_SOURCE` is flipped to `skribby`, OpenAI stops receiving the audio (except for the sampled shadow share): the OpenAI sentence must change in the same pass.** Same if `SKRIBBY_TRANSCRIPTION_MODEL` changes (the ElevenLabs sentence) or the bot provider changes again.

## Follow-ups

- The lawyer's questions are in the PR body (cross-border transfer under the NDPA; ElevenLabs' training terms; deletion that does not reach Skribby's debugging data).
- Confirm `SKRIBBY_REGION` on the server; add it to the policy if wanted.
- Tell the lead before merge: the "Last updated" date will read 14 November even if the merge is later (it must equal `TERMS_VERSION`; he decides whether to move both).
