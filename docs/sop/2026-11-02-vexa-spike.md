# SOP: Vexa spike

## What shipped
A local spike of self-hosted Vexa on branch `chore/vexa-spike`. Not committed at the time of writing. Full results are in [the spike report](../spike/vexa-spike-report.md).

## Why
To prove that a pasted meeting link can produce notes across Google Meet and Zoom before building on Vexa. Zoom support was the main risk.

## How
- Used Vexa Lite because Docker has about 2.8 GB of memory here and the full stack runs about 8 containers.
- Used OpenAI for transcription and left GPU self-hosting out.
- Compared live `whisper-1` with `gpt-transcribe` on saved recordings.
- Rejected the live transcript as the source for notes because it changed meanings and invented details.

## What's involved

| Path | Purpose | In git |
|---|---|---|
| `spike/vexa/` | Vexa clone at `31122f9` | No |
| `spike/vexa/.env` | Generated secrets and OpenAI key | No |
| `spike/.env.api` | Local Vexa API key | No |
| `spike/captures/raw/` | Call audio, transcripts, notes | No |
| `docs/spike/vexa-spike-report.md` | Results and decision | Yes |
| `docs/checklist/master-checklist.md` | Build checklist | Yes |

## Verification
Three real calls: one Google Meet and two Zoom, including another account hosting with a waiting room. Notes were generated from the last Zoom call. Real OpenAI spend has not been checked.

## Operate / roll back
Run from `spike/vexa`.

Start:

```bash
make -C deploy/lite up
```

Stop, keeping data:

```bash
make -C deploy/lite down
```

Remove the stored recordings and database:

```bash
docker volume rm vexa-lite-pgdata vexa-lite-storagedata
```

## Follow-ups
See the limits section of the spike report and the deferred items in the master checklist.
