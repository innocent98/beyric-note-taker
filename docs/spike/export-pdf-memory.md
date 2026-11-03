# Spike EX1: does WeasyPrint fit the image, and the memory, of the web container?

**Written:** 2026-11-03 (AI workspace spec phase W5, `docs/superpowers/specs/2026-11-03-ai-workspace-design.md` section 9, decision D9).
**Verdict: it fits. Build the PDF export with WeasyPrint, in a child process, and enable "include the transcript".** No alternative needed.

Evidence labels: *measured* (a number I read from a run today, method below), *read* (a file or command output), *not measured* (said so).

## Question
Spec section 9 left two things *unverified*: WeasyPrint needs the Pango libraries in the `python:3.13-slim` image, and long documents use real memory. Decision D9 said the "with transcript" option stays off until this is measured, and offered a fallback (first N pages with a notice, or build in the worker) if it is too heavy.

## Findings

### 1. The image: nothing to add to apt, one pip layer
- The app image installs `ffmpeg` from apt. `apt-get install -s --no-install-recommends` for `ffmpeg` and for `ffmpeg libpango-1.0-0 libpangoft2-1.0-0` both report **202 packages**: ffmpeg already depends on Pango, Cairo, HarfBuzz and fontconfig (through `libass` and `librsvg`) *(measured, apt dry run on `python:3.13-slim`)*. The `Dockerfile` now names the two Pango packages anyway, so the export does not break if ffmpeg's dependencies ever change.
- The pip side is WeasyPrint 70.0 and python-docx 1.2.0 with their dependencies (lxml, fonttools, pydyf, cffi, tinycss2, cssselect2, pyphen, brotli, zopfli): a **103 MB layer** in the spike image *(measured, `docker history`)*. The finished app image (`docker build .` of this branch) is **264 MB** on disk *(measured, `docker image inspect`; arm64 on a Mac, the server is x86 so expect a similar size)*.
- Fonts: the PDF uses the app's own Geist files (`app/static/fonts/`, variable weight). WeasyPrint embeds them and picks the weights correctly (Geist, Geist-Bold, Geist-Semi-Bold, Geist-Mono in `pdffonts`). Characters Geist's Latin file lacks (Yoruba dot-below letters, for example) fall back to DejaVu from the image's fontconfig package; checked with "ẹ ọ ṣ" and "Yorùbá" *(measured, rendered and looked at)*.
- A trap, found by running it: the image's code is run from `/srv` (copied there) while `pip install .` puts a second copy in site-packages **without `app/static/`**. A child process started with `python -I -m app.export_render` imports the site-packages copy and cannot find the fonts. The export therefore starts the child with `cwd=/srv`, `python -m` (no `-I`), so it imports the same `app` package as the web process.

### 2. Memory and time
Method. The spike script (`docs/spike/export-pdf/render.py`) renders a synthetic meeting (the six-section notes plus a seeded random transcript, `[Name]: ...` turns of 40 to 120 words) and reports `ru_maxrss` of that process. Then the real thing: `scripts/capture_export_api.py` runs the real app and calls the real endpoint inside the real app image, with `docker run --memory=512m --memory-swap=512m --cpus=2`, as the `app` user, **one export at a time**, and reads the renderer's own peak from the app's log line (`VmHWM` from `/proc`; Linux's `ru_maxrss` counts the parent the child was forked from, which gave a false constant 178 MiB until I changed it). A 150 words-a-minute speaker gives about 9,000 words in an hour.

**Through the endpoint, in the app image** (`docs/fe-captures/export/timings.json`, *measured*; arm64 Docker VM on a Mac under heavy load from other sessions, load average 16 to 27, so seconds are noisy and an upper-ish bound for a quiet machine):

| Transcript | Pages (spike render) | PDF notes only | PDF with transcript | Word with transcript |
|---|---|---|---|---|
| 1 hour (9,000 words) | 21 | 1.5 s, 86 MiB | **1.9 s, 91 MiB** | 0.5 s, 45 MiB |
| 3 hours (27,000 words) | 59 | 1.5 s, 86 MiB | **5.0 s, 103 MiB** | 1.1 s, 46 MiB |
| 40,000 words (the spec's figure) | 86 | 2.2 s, 86 MiB | **9.0 s, 120 MiB** | 1.5 s, 47 MiB |

Earlier runs of the bare spike script in the same kind of container gave the same picture: 1 hour 81 to 90 MiB and 1.1 to 5.7 s (first run slow, then warm), 3 hours 92 to 108 MiB, 40,000 words 107 to 131 MiB and 3 to 8 s, Word 32 to 34 MiB. The same code natively on the Mac, with the machine quiet: 0.7 s notes only, 1.2 s for the 1-hour PDF, 2.6 s for 40,000 words.

Reading:
- The PDF renderer holds **about 90 MiB before any transcript and grows about 1 MiB per 1,000 to 1,800 words**; 40,000 words peaks at 120 MiB. The web process itself is about 86 MiB idle *(read, `docs/spike/nextjs-scaffold/memory-and-deploy.txt`)*, so the worst case is about 210 MiB for the container while one export runs. The whole run (app, client, renderer) stayed inside a 512 MiB cap with room.
- **`compose.yaml` gives the `web` service no `mem_limit`** (only `next` has 512m and `vexa` 3g). The brief for this task said it had one. So there is nothing to fit under, and nothing stops a runaway; the export adds its own guard (below). Recommendation, not done here (deployment config is the lead's call): `mem_limit: 512m` on `web`.
- Time is dominated by layout and grows with pages (about 1 s a hour of speech, when the machine is quiet). The longest measured, 9 s on a loaded machine, is well under the 45 s limit below. A first export after a restart is a few seconds slower (fontconfig and page cache warm up).

### 3. What the build does about it
| Control | Value | Why |
|---|---|---|
| Child process per export | `python -m app.export_render pdf\|docx`, document as JSON on stdin | A hung render is killed. The memory a large document needed goes back to the system instead of staying in a long-lived process (glibc rarely returns it). The child never sees the web process's environment (database password, API keys): `_child_env` passes only `PATH` and the library path |
| One at a time | a process-wide semaphore, taken without waiting | A second request gets 503 `export_busy` with `Retry-After: 5`. With one web process and about ten accounts that is rare and honest |
| Time limit | **45 s**, not the spec's 60 s | `deploy/nginx.conf` has `proxy_read_timeout 60s`: a 60 s render would reach the visitor as nginx's gateway error, not our JSON. At 45 s the app answers first (504 `export_timeout`) |
| Memory limit | `RLIMIT_AS` 2 GiB in the child on Linux | Address space runs well above resident memory, so this is far above the 120 MiB peak; it only stops a runaway |
| Logging | one line per export: format, seconds, bytes, the renderer's peak MiB | So a slow or heavy export shows in the logs; no notes content is logged |

## Decision
- **WeasyPrint for PDF and python-docx for Word, as D9 recommended.** "Include the full transcript" is enabled for both formats; no page limit is needed.
- Alternatives not needed: headless Chromium (about 400 MB more image), ReportLab (all layout by hand), building in the worker.
- If a meeting ever produces a document that does not finish in 45 s (not seen: 40,000 words is 9 s on a loaded machine), the user gets the 504 message "Try again, or leave the transcript out", and a Word file of the same transcript takes about 1.5 s.

## Not measured
- A real x86 server under real load: all numbers are an arm64 Docker VM. The relationship (renderer RSS about 90 to 120 MiB) should hold; the seconds may be 1.5 to 2 times on the 4-vCPU shared box.
- Two users exporting at once (the semaphore answers the second one with 503 by design; that was proved live, `docs/fe-captures/export/16-export-busy.json`).
- A real transcript with more complex notes than the synthetic text (the layout cost is per word and per list item; the notes are a few hundred words).

## Reproduce
```bash
docker build -t nt-ex1 docs/spike/export-pdf
docker run --rm --user 999:999 --tmpfs /tmp:rw,size=256m,mode=1777 --memory=768m \
  -v "$PWD/docs/spike/export-pdf:/spike:ro" -v "$PWD/app/static/fonts:/fonts:ro" \
  nt-ex1 python -I /spike/render.py 9000 pdf --repeat 3
```
The endpoint numbers: build the app image (`docker build -t nt-w5-app .`), start a scratch Postgres, then
`docker run --user app -e PYTHONPATH=/srv -w /srv -v "$PWD/scripts:/srv/scripts:ro" -v "$PWD/docs:/srv/docs" nt-w5-app python /srv/scripts/capture_export_api.py postgresql+psycopg://USER:PASS@host.docker.internal:PORT/postgres`.
(On a Docker VM whose disk is full, add `--tmpfs /tmp`: WeasyPrint writes the web-font files it uses to `/tmp`, and the first runs of this spike failed with "No space left on device" until I did.)
