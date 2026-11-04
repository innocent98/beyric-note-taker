# Frontend integration guide: Translate (W7)

Every body, status code and error string below is pasted from a response captured from the running app by `scripts/capture_translate_api.py`: real HTTP, a scratch Postgres with the real migrations, the real routes. Files are in `docs/fe-captures/translate/` (`NN-name.json`, request and response); the number in each heading is the capture. Long strings are cut with `…` and the meeting detail is shown with only the fields this feature touches (the capture holds the rest). **One thing is not real: the translating model is a fake** that answers with the designer's own French and Spanish for the designer's own sample meeting, so nothing here says how good a real model's French is (that is the TR1 eval, not yet run: see the last section). Placeholder data throughout (`owner@example.test`, `<csrf-A>`).

## 1. What it is, and when it happens

A person opens a ready meeting and picks **French** or **Spanish** in the Language select on the notes toolbar. The server translates the notes **and** the transcript (the original is never changed), keeps the result, and the page shows it with "Translated from English to French · Show original". Nothing is translated unless the person asks (the select, "Translate to French", "Translate again", "Try again"), except that a translation that already exists opens by itself when it is the account's default language (Settings, "Default notes language").

- The work runs in the background. A request answers at once; the page asks again every **2 seconds** until the translation is `ready` or `failed`. A long meeting takes minutes.
- Only **French and Spanish** are translated into. **English is the original**: it is a choice in the select ("English (original)") that shows the original, never a request.
- A translation is cached per meeting and language. If the notes are rewritten ("Rewrite notes"), the translation becomes **stale**.
- 20 new translations a day per account.

Auth, CSRF and the error shape are as in `docs/fe-integration-guide-owner-app.md`: session cookie, `X-CSRF-Token` on every non-GET, errors `{"error": {"code", "message", "fields?"}}`. A meeting that is not the caller's is **404 `not_found`**, never 403.

## 2. Endpoints

### 2.1 `POST /api/meetings/{id}/translations`: ask for one

Body `{"language": "fr" | "es"}`. **200** when a finished translation of the *current* text is already there; **202** when it was queued, is already being made, or is a failed or stale one asked for again.

`POST /api/meetings/1/translations  {"language": "fr"}` → **202**

```json
{
  "language": "fr",
  "status": "translating",
  "stale": false,
  "notes": null,
  "notes_html": null,
  "transcript": null,
  "failure_code": null,
  "failure_message": null,
  "updated_at": "2026-11-04T16:08:52.659660Z"
}
```

Asking again while it is being made changes nothing:

`POST /api/meetings/1/translations  {"language": "fr"}` → **202**

```json
{
  "language": "fr",
  "status": "translating",
  "stale": false,
  "notes": null,
  "notes_html": null,
  "transcript": null,
  "failure_code": null,
  "failure_message": null,
  "updated_at": "2026-11-04T16:08:52.659660Z"
}
```

*Capture note: Asking again while it is being made changes nothing: still 202 translating.*

When it is finished, the same request is served from the cache with the whole translation (200):

`POST /api/meetings/1/translations  {"language": "fr"}` → **200**

```json
{
  "language": "fr",
  "status": "ready",
  "stale": false,
  "notes": "## Résumé\nLa réunion a présenté Beyric Note Taker, une application dév…",
  "notes_html": "<h2>Résumé</h2>\n<p>La réunion a présenté Beyric Note Taker, une applic…",
  "transcript": "[Adebayo]: Alors, je vais juste vous présenter l'application.\n[Adebayo…",
  "failure_code": null,
  "failure_message": null,
  "updated_at": "2026-11-04T16:08:52.688638Z"
}
```

### 2.2 `GET /api/meetings/{id}/translations/{language}`: poll and read

While it is being made:

`GET /api/meetings/1/translations/fr` → **200**

```json
{
  "language": "fr",
  "status": "translating",
  "stale": false,
  "notes": null,
  "notes_html": null,
  "transcript": null,
  "failure_code": null,
  "failure_message": null,
  "updated_at": "2026-11-04T16:08:52.659660Z"
}
```

When it is done (`ready`). `notes` is Markdown whose six headings come from a fixed dictionary; `notes_html` is the same rendered on the server with **links and raw HTML switched off**; `transcript` has the original's lines and `[Name]:` labels, as plain text:

`GET /api/meetings/1/translations/fr` → **200**

```json
{
  "language": "fr",
  "status": "ready",
  "stale": false,
  "notes": "## Résumé\nLa réunion a présenté Beyric Note Taker, une application dév…",
  "notes_html": "<h2>Résumé</h2>\n<p>La réunion a présenté Beyric Note Taker, une applic…",
  "transcript": "[Adebayo]: Alors, je vais juste vous présenter l'application.\n[Adebayo…",
  "failure_code": null,
  "failure_message": null,
  "updated_at": "2026-11-04T16:08:52.688638Z"
}
```

*Capture note: `notes` has the dictionary's headings; `transcript` has the original's lines.*

Spanish has the same shape:

`GET /api/meetings/1/translations/es` → **200**

```json
{
  "language": "es",
  "status": "ready",
  "stale": false,
  "notes": "## Resumen\nLa reunión presentó Beyric Note Taker, una aplicación en de…",
  "notes_html": "<h2>Resumen</h2>\n<p>La reunión presentó Beyric Note Taker, una aplicac…",
  "transcript": "[Adebayo]: Bueno, solo voy a presentarles la aplicación.\n[Adebayo]: Es…",
  "failure_code": null,
  "failure_message": null,
  "updated_at": "2026-11-04T16:08:52.745214Z"
}
```

The headings are always these, in the original's order: French "Résumé", "Points clés", "Décisions", "Actions à mener", "Questions ouvertes", "À vérifier"; Spanish "Resumen", "Puntos clave", "Decisiones", "Tareas", "Preguntas abiertas", "Revisar". A section that has nothing reads "Aucun point noté." / "Nada que anotar." (under Action items: "Aucune action notée." / "No se anotaron tareas.", the designer's words).

A translation that did not pass the server's checks (a line was lost, a number or name changed, a link appeared) is **kept as `failed`, never shown**; `failure_message` is a sentence to show as it is:

`GET /api/meetings/1/translations/es` → **200**

```json
{
  "language": "es",
  "status": "failed",
  "stale": false,
  "notes": null,
  "notes_html": null,
  "transcript": null,
  "failure_code": "structure",
  "failure_message": "The translation did not come out right, so it was not kept. You can tr…",
  "updated_at": "2026-11-04T16:08:53.377378Z"
}
```

*Capture note: Provoked inside the capture: the fake model dropped a line, so the check failed.*

`failure_code` values (switch on the code, show the message): `structure`, `markup`, `names`, `numbers`, `model_reply`, `model_error`, `too_long`, `gave_up`, `not_ready`, `error`. **Only `structure` was exercised live (capture 42, provoked by the fake model); the others share one code path and one sentence table (`app/translation.py`, `FAILURE_TEXT`) and are covered by unit tests, not by captures.**

After the notes were rewritten, a finished translation is still returned, with `stale: true`:

`GET /api/meetings/1/translations/fr` → **200**

```json
{
  "language": "fr",
  "status": "ready",
  "stale": true,
  "notes": "## Résumé\nLa réunion a présenté Beyric Note Taker, une application dév…",
  "notes_html": "<h2>Résumé</h2>\n<p>La réunion a présenté Beyric Note Taker, une applic…",
  "transcript": "[Adebayo]: Alors, je vais juste vous présenter l'application.\n[Adebayo…",
  "failure_code": null,
  "failure_message": null,
  "updated_at": "2026-11-04T16:08:52.688638Z"
}
```

Asking again for a stale (or failed) one queues it again (202, `stale: false`, `notes` back to `null` until it is ready):

`POST /api/meetings/1/translations  {"language": "fr"}` → **202**

```json
{
  "language": "fr",
  "status": "translating",
  "stale": false,
  "notes": null,
  "notes_html": null,
  "transcript": null,
  "failure_code": null,
  "failure_message": null,
  "updated_at": "2026-11-04T16:08:53.323634Z"
}
```

A language that was never asked for, and any language that is not offered, is 404 `no_translation`:

`GET /api/meetings/4/translations/fr` → **404**

```json
{
  "error": {
    "code": "no_translation",
    "message": "This meeting has no translation yet."
  }
}
```

`GET /api/meetings/1/translations/yo` → **404**

```json
{
  "error": {
    "code": "no_translation",
    "message": "This meeting has no translation yet."
  }
}
```

### 2.3 `DELETE /api/meetings/{id}/translations/{language}`: throw it away

204 with no body. It also cancels one still queued. It does **not** give back one of the day's 20.

`DELETE /api/meetings/1/translations/es` → **204**

```json
null
```

`DELETE /api/meetings/1/translations/es` → **404**

```json
{
  "error": {
    "code": "no_translation",
    "message": "This meeting has no translation yet."
  }
}
```

### 2.4 The meeting carries what exists

`GET /api/meetings/{id}` has a new field, `translations`, one entry per language that has a row in any state, so the page knows what to open and what to offer without asking. Empty until something was asked for:

`GET /api/meetings/1` → **200**

```json
{
  "notes": "## Summary\nThe meeting introduced Beyric Note Taker, an app being deve…",
  "notes_html": "<h2>Summary</h2>\n<p>The meeting introduced Beyric Note Taker, an app b…",
  "transcript": "[Adebayo]: So, I'm just going to introduce the app to you.\n[Adebayo]: …",
  "translations": []
}
```

*Capture note: Nothing translated yet.*

`GET /api/meetings/1` → **200**

```json
{
  "notes": "## Summary\nThe meeting introduced Beyric Note Taker, an app being deve…",
  "notes_html": "<h2>Summary</h2>\n<p>The meeting introduced Beyric Note Taker, an app b…",
  "transcript": "[Adebayo]: So, I'm just going to introduce the app to you.\n[Adebayo]: …",
  "translations": [
    {
      "language": "fr",
      "status": "ready",
      "stale": false
    }
  ]
}
```

### 2.5 Default notes language: `GET` and `PUT /api/settings/language`

`en` means "the original, no translation" (the default, also for an account that has no settings row yet). The change is saved on the server at once; it translates nothing.

`GET /api/settings/language` → **200**

```json
{
  "default_language": "en"
}
```

`PUT /api/settings/language  {"default_language": "fr"}` → **200**

```json
{
  "default_language": "fr"
}
```

`GET /api/settings/language` → **200**

```json
{
  "default_language": "fr"
}
```

`PUT /api/settings/language  {"default_language": "yo"}` → **422**

```json
{
  "error": {
    "code": "language_unsupported",
    "message": "Notes can be translated into French or Spanish. The original is the En…"
  }
}
```

`PUT /api/settings/language  {}` → **422**

```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "default_language",
        "message": "Field required"
      }
    ]
  }
}
```

### 2.6 Download in a language: `GET /api/meetings/{id}/export?...&language=fr|es|en`

`language` is new on the existing export route. `en` (the default) is the original. `fr` or `es` builds the file from the finished, current translation (notes, and the transcript if `transcript=true`). The file's headings are the translated ones; the footer line ("Generated by Beyric Note Taker, 4 Nov 2026") and the date and platform line stay English.

`GET /api/meetings/1/export?format=docx&transcript=true&language=fr` → **200**

```json
null
```

*Capture note: Built from the cached French translation.*

`GET /api/meetings/1/export?format=docx&language=en` → **200**

```json
null
```

No finished translation yet, or an out-of-date one, is **409 `translation_missing`** (the page can only offer the language it is showing, so this is a race: refetch and offer Translate again):

`GET /api/meetings/4/export?format=docx&language=fr` → **409**

```json
{
  "error": {
    "code": "translation_missing",
    "message": "Translate the notes to French first."
  }
}
```

`GET /api/meetings/1/export?format=docx&language=fr` → **409**

```json
{
  "error": {
    "code": "translation_missing",
    "message": "The French translation is out of date. Translate again first."
  }
}
```

*Capture note: A stale translation is not downloaded.*

An unknown language is the generic validation error (this route declares the three codes, so it is not `language_unsupported`):

`GET /api/meetings/1/export?format=docx&language=yo` → **422**

```json
{
  "error": {
    "code": "validation_error",
    "message": "Some fields are missing or not valid.",
    "fields": [
      {
        "field": "language",
        "message": "Input should be 'en', 'fr' or 'es'"
      }
    ]
  }
}
```

## 3. Every error the page must handle

| Where | Status | `code` | `message` (as returned) |
|---|---|---|---|
| any route, no login | 401 | `not_authenticated` | "Log in first." (capture 02) |
| POST, a language that is not `fr` or `es` (including `en`, `""`, `"FR"`) | 422 | `language_unsupported` | "Notes can be translated into French or Spanish. The original is the English version." (23, 24) |
| POST, body without `language` | 422 | `validation_error` | "Some fields are missing or not valid." with `fields: [{"field": "language", "message": "Field required"}]` (25) |
| POST, meeting not ready | 409 | `not_ready` | "The notes are not ready yet." (26) |
| POST, the 21st of the day | 429 | `translation_limit` | "You have used today's 20 translations. Try again tomorrow." (48) |
| GET, DELETE, nothing there (or unsupported language) | 404 | `no_translation` | "This meeting has no translation yet." (27, 28, 46, 47) |
| any route, someone else's or a missing meeting | 404 | `not_found` | "No such meeting" (30 to 33) |
| PUT language, unsupported | 422 | `language_unsupported` | as above (20) |
| PUT language, no field | 422 | `validation_error` | (21) |
| export, no current translation | 409 | `translation_missing` | "Translate the notes to French first." (29) / "The French translation is out of date. Translate again first." (37) |

The shared client's `errorMessage()` shows the server's message for every status except 404, 405 and 422 (developer text there; the page never sends a language it does not offer, so those cannot happen from the UI). **429 and 409 are shown verbatim.**

Not captured live (not reproducible from outside): a translation job lost by a dead worker (a claim older than 45 minutes is taken again, three claims at most, then `failed` / `gave_up`), covered by unit tests only.

## 4. Field-nesting traps

- `translations` (on the meeting) holds only `{language, status, stale}`; the words are **only** on `GET .../translations/{language}`. Do not look for `notes` there.
- `notes`, `notes_html`, `transcript` are **present and `null`** (not absent) unless `status` is `ready`. `failure_code` and `failure_message` are present and `null` unless `failed`.
- `stale` is on both the brief and the full object, and is `true` only for a `ready` translation whose source text changed.
- The meeting's own `notes_html` renders links (`rel="noopener noreferrer nofollow"`); the translation's `notes_html` **never contains `<a>`**: link syntax in the original shows as literal text in the translation. This is on purpose.
- `transcript` of a translation is plain text, one line per original line, labels intact: parse it with the same `parseTranscript` as the original. A meeting with no transcript has `transcript: null` in the translation.
- The request's `language` can be only `fr`/`es`; the select's `en` is a client-side "show original" and is never sent. The settings value uses all three (`en` is the original).

## 5. State machine and polling

| State | What the page does |
|---|---|
| none (`404 no_translation`, or absent from `translations`) | Original on screen. Select on "English (original)". If the default language is `fr`/`es` and none exists: one line "Your default notes language is French." with a **Translate to French** button. Nothing is requested on its own |
| `translating` | Keep the original on screen, show "Translating to French..." and ask `GET .../fr` **every 2 seconds** (no back-off needed; a lost connection is not a failure: keep asking) |
| `ready`, `stale: false` | Show the translation, the bar "Translated from English to French" and **Show original** |
| `ready`, `stale: true` | Show it, with the bar and a second line "The notes were rewritten after this was translated." and **Translate again** (POST again) |
| `failed` | Show `failure_message` with **Try again** (POST again), original on screen |

Shown language drives Copy (copies the shown notes or transcript), the Transcript tab, and the Download menu note ("In French, with summary, decisions, action items and owners.") and the `language` it sends. Opening a meeting whose default-language translation is `ready` shows it at once; if it is still `translating`, it keeps polling.

## 6. What the person will experience that the page must say, not hide

- **It takes time.** A short call is seconds, an hour-long meeting minutes. Keep "Translating to French..." visible and the original readable meanwhile.
- **It is machine translation of a machine-written text.** The original stays the record, and "Show original" is always one click away. The terms of service rows for this are with the lead (see the SOP).
- **A translation can be refused by the server's checks** and shows as failed rather than wrong. Offer Try again; do not show partial text.
- **Rewriting the notes makes the translation out of date.** Do not silently keep showing it as current: show the stale line.
- **The daily limit** (20) is per account and is not given back by Delete. Show the 429 sentence as returned.
- **Names, numbers and links.** Speaker names, the owner's vocabulary and every number are checked to survive unchanged; links in the original become plain text in the translation.
- **Translations are not shared.** Only the owner can read, ask for or delete them.

## 7. Verification table

| Behaviour | Verified live (capture) |
|---|---|
| POST 202 queue, repeated POST while working 202, POST 200 cached | yes (07, 09, 11) |
| GET translating, ready (French, Spanish), failed (`structure`), stale, delete, 404s | yes (08, 10, 14, 42, 36, 45 to 47, 27, 28) |
| `translations` on the meeting detail | yes (05, 12) |
| Default language read, write, unsupported, missing | yes (06, 18 to 22) |
| Export in French (DOCX), original, 409 missing, 409 stale, 422 unknown | yes (15 to 17, 29, 37) |
| Foreign and missing meeting: 404 on every translation route | yes (30 to 33) |
| 401 without a login, 422, 409 `not_ready`, 429 limit | yes (02, 23 to 26, 48) |
| Stale after "Rewrite notes" (real route, notes model faked) | yes (35, 36) |
| Failure codes other than `structure` | **no**: unit tests only |
| A claim lost by a dead worker, `gave_up` | **no**: unit tests only |
| **Real translation quality (French and Spanish, names intact), real token cost, real duration of an hour-long meeting** | **no: the model is a fake in every capture. `tests/eval/translation_eval.py` (TR1) is written and was not run (no `OPENAI_API_KEY` where this was built)** |
| PDF in a language | **no**: only DOCX was captured; PDF and DOCX share the document model, and the PDF renderer is covered by the W5 captures and tests |
| The page against the real API in a browser | **no**: screens were checked against `web/scripts/mock-api.mjs`, which replays these captures (`docs/ui/parity-report.md`) |
