# Primary pages read by the thirteenth lead, 2026-11-07

Each file is the visible text of a vendor page, downloaded with `curl` (raw HTML, not a model's summary) and stripped of tags by a small script. Dropped tags can hide content that only a browser shows (pricing tables built by scripts, tabs): read these as "what the HTML contained", and open the live page before a purchase decision.

| File | Page |
|---|---|
| `skribby-pricing.txt` | https://skribby.io/pricing |
| `skribby-docs-billing.txt` | https://skribby.io/docs/guides/billing (plan limits: Free plan 5 monthly bot hours, 3 concurrent bots) |
| `skribby-docs-bot-authentication.txt` | https://skribby.io/docs/guides/bot-authentication |
| `skribby-docs-quickstart.txt` | https://skribby.io/docs/guides/quickstart |
| `recall-pricing.txt` | https://www.recall.ai/pricing ("your first 5 hours of recording are free" on the Pay As You Go plan) |

## Added 2026-11-07 (late evening) for the bot-hosting analysis

Written for `docs/spike/bot-hosting-cost-stress-infra-2026-11-08.md`. Same method: `curl -sL -A "Mozilla/5.0"`, raw `.md` pages and GitHub API answers saved untouched, HTML pages stripped of tags; each file starts with its `SOURCE:` URL and fetch date. Files marked `-reread` are a second download of a page already saved above (Skribby and Recall pricing), kept so the analysis cites one page read on the same evening.

| Group | Files | Pages |
|---|---|---|
| Vexa (hosted offer, docs) | `vexa-pricing.txt`, `vexa-docs-llms.md`, `vexa-docs-deployment-lite.md`, `vexa-docs-authenticated-bots.md`, `vexa-docs-roadmap-status.md`, `vexa-docs-quickstart.md`, `vexa-site-privacy.txt`, `vexa-subprocessors.txt` | vexa.ai/pricing, docs.vexa.ai/..., vexa.ai/privacy, vexa.ai/legal/subprocessors |
| Vexa (GitHub API, JSON) | `vexa-github-issue-1725*.json`, `vexa-github-pr-1873*.json`, `vexa-github-releases.json`, `vexa-github-repo.json` | issue 1725, PR 1873 (state, `mergeable_state`, comments), releases, repo |
| Recall | `recall-docs-*.md` (Zoom, Teams, storage, usage, index), `recall-data-processing-agreement.txt`, `recall-privacy.txt`, `recall-pricing-reread.txt` | docs.recall.ai/docs/..., recall.ai/data-processing-agreement, recall.ai/privacy, recall.ai/pricing |
| Skribby | `skribby-guide-{regions,transcription-privacy,team-management,best-practices}.txt`, `skribby-privacy-policy.txt`, `skribby-sub-processors.txt`, `skribby-pricing-reread.txt` | skribby.io/docs/guides/..., /privacy-policy, /sub-processors, /pricing |
| Zoom | `zoom-meeting-sdk-home.txt`, `zoom-meeting-sdk-auth.txt`, `zoom-obf-blog.txt`, `zoom-changelog-auth.txt`, `zoom-rtms.txt` | developers.zoom.us (Meeting SDK, OBF blog, changelog, RTMS) |
| Microsoft, Google | `microsoft-teams-lobby.txt`, `microsoft-teams-meeting-settings.txt`, `google-meet-join-without-asking.txt`, `google-workspace-pricing.txt` | learn.microsoft.com Teams lobby and meeting settings, support.google.com/a/users/answer/12172260, workspace.google.com/pricing |
| Machines | `hetzner-cloud-shared-plans.txt`, `hetzner-cloud-dedicated-plans.txt`, `contabo-vps.txt` | hetzner.com/cloud pages, contabo.com/en/vps |

Not read: Zoom's Meeting SDK authorization FAQ (404), `skribby.io/terms|dpa|security|status` (404), `vexa.ai/terms|dpa` (404), Recall's trust portal and sub-processor list (script-loaded, saved text empty), Linode and Vultr pricing (403).

