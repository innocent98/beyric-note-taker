# SOP: Audio preparation (Task 2)

## What shipped
`prepare_chunks(source, out_dir) -> list[Path]` in `app/audio.py`: compresses a Vexa recording to mono 32 kbps Opus and splits it into `.webm` chunks of at most 15 minutes, each well under OpenAI's 25 MB limit. Also adds ffmpeg to the `Dockerfile` and the CI workflow. Commit: `feat: prepare meeting audio for transcription`.

## Why
An hour of Vexa audio is about 56 MB, over OpenAI's 25 MB upload limit. At 32 kbps mono an hour is about 14 MB, and 15-minute chunks are about 3.6 MB each.

## How
1. Encode the source once to `_full.webm` (mono, libopus, 32 kbps, `voip` mode) in `out_dir`.
2. One ffmpeg pass over it runs `volumedetect` and `silencedetect` (-35 dB, 0.3 s). A loudest sample below -50 dB raises `NoSpeechError`.
3. Cut points: for each window, take the silence overlapping the last 30 s that is nearest the window end (cut at the middle of the overlap). With no silence, cut at the window end.
4. Each chunk is encoded from the original with `-ss`/`-t`. Stream-copying `_full.webm` was tried and rejected: copy cuts land on container clusters and a chunk came out 3 s over its length.
5. The window is 899 s, not 900 s, so container rounding cannot push a chunk past 15 minutes.

Errors: ffmpeg or ffprobe non-zero exit, timeout (600 s / 60 s), missing binary, missing input, or no audio stream raise `AudioError` with the command and stderr. `NoSpeechError` subclasses `AudioError`. Partial chunks are deleted on failure; `_full.webm` is always removed.

## What's involved

| Path | Purpose |
|---|---|
| `app/audio.py` | `prepare_chunks`, `AudioError`, `NoSpeechError` |
| `tests/test_audio.py` | Hour split, mono Opus, cut inside silence, cut without silence, short file, silent file, corrupt, missing, timeout |
| `Dockerfile` | `apt-get install ffmpeg` |
| `.github/workflows/ci.yml` | `apt-get install ffmpeg` before `pip install` |

Local: `brew install ffmpeg`.

## Verification
`tests/test_audio.py` generates tone audio with ffmpeg once per session (an hour, 20 minutes with a 1 s gap at 14:45, 10 s of silence). The gap test proves the first chunk ends between 885 s and 886.1 s. The suite, `ruff check` and `ruff format --check` pass.

## Operate / roll back
Revert the commit. The worker (Task 5) should create a per-meeting temp `out_dir` and delete it after transcription.

## Follow-ups
- The `-35 dB` silence and `-50 dB` no-speech thresholds are untuned against real meetings; check in Task 8.
- Test audio is tone, not speech.
- Docker image not built here; CI does not build it.
