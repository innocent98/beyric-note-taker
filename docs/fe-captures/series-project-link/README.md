# Captures: linking a recurring meeting to a project, and the Series page's sprint line

Written by `python scripts/capture_series_project_api.py <postgres admin url>` against the running app; the guide is `docs/fe-integration-guide-series-project-link.md`. Every file is one real request and response (`NN-name.json`, with a `note`); `38` and `39` are the queued and rendered one-hour reminder (mail provider replaced by a capturing mailer).

Redacted: **passwords are replaced by `<password placeholder>`** in the sign-in request bodies, CSRF tokens by `<csrf-A>` and so on, and the session cookie by `<cookie>`. Names and addresses are placeholders (`@example.test`). Nothing else is edited.
