# Fix: choosing the default team on a team page did not stick

**What shipped.** One PR (branch `fix/team-switch-team-pages`; number in the checklist, "Multi-team"). Switching team from the sidebar or top-bar switcher on `/`, `/tasks`, `/decisions` and `/review` (and `/team`, `/projects`, `/settings`) now stays on the team chosen. Follow-up to `docs/sop/2026-11-14-team-scoped-lists-and-nav.md` (the full-page-load switch). No API, schema or migration change; no change to what the FE guide documents.

## Why (the proven cause)

Adebayo's report (live, 2026-11-14): choosing another team put him back on the personal team on `/`, `/tasks`, `/decisions`, `/review`; neutral pages (Ask, Notifications) were fine. Reproduced in a real browser against `web/scripts/mock-api.mjs`. **Choosing the person's DEFAULT team was undone**, from any other team. The default team is the personal team when the person has two or more ordinary teams, and the ordinary team when they have exactly one, so for him "personal → the other team" was exactly "→ the default".

Mechanism, from a trace of every `sessionStorage` write during one switch (shape: personal + one ordinary team, default = the ordinary team, page `/?team=1`, choose team 2):

```
2776ms@/?team=1  REMOVE  ← writeStoredTeam ← choose()                 (the switcher forgets the choice: the default is "no ?team=")
2782ms@/?team=1  SET :1  ← writeStoredTeam ← ActiveTeamSync.useEffect (the sync puts the OLD team back, 6 ms later)
```

`choose()` removes the stored team and dispatches `beyric:active-team`. `ActiveTeamSync` re-ran on that change; the page on screen still had `?team=1` (the load had not committed), and its rule "the address names a team of yours, so storage follows the address" wrote team 1 back. `/` then loaded with no `?team=`, found team 1 remembered and replaced itself with `/?team=1` (the captured URL sequence was `/`, `/`, `/?team=1`). Choosing a non-default team survived because its destination carries `?team=` and wins on load. Neutral pages were fine because the sync ignores them.

## How

| Change | Where |
|---|---|
| `planSwitch` (what a choice stores and which address it loads) and `planSync` (what a page does about the tab's team on load) are **pure functions**, so tests run the real rules | `web/lib/active-team.ts` |
| `beginTeamSwitch()` marks the page as leaving; `ActiveTeamSync` does nothing while it is set (cleared on `pageshow`, for the back-forward cache). Also covers a `router.refresh()` that hands the sync a new `teams` array during the load | `web/lib/active-team.ts`, `components/app/team-switcher.tsx`, `components/app/active-team-sync.tsx` |
| Mock API: `--teams one` (personal + one ordinary team, which is then the default), `--log-team` and `GET /__mock/teamlog` (X-Team-Id of each call), meeting titles carry the team named; **`refresh_after_seconds: null`** added to the meetings list (it was missing, so `setInterval(refresh, NaN)` made the Meetings page refresh in a tight loop against the mock) | `web/scripts/mock-api.mjs` |

Rejected: making the switcher not write storage on team pages (the default team needs storage empty before the load, and neutral pages need it written); keying the sync effect on the URL only (the initial storage read needs `stored` as a dependency, and a refresh would still re-run it).

## Verification

- **Browser (Playwright against the mock, Next dev server), before and after the fix.** Before: `/` and `/tasks` 3→personal ended on `?team=3`; shape one, personal→default ended on `?team=1`. After: for `/`, `/tasks`, `/decisions`, `/review`, in shape "personal + two teams" (personal→2, 2→3, 3→personal, personal→2) and shape "personal + one team" (default→personal, personal→default), the URL, `sessionStorage` `beyric.active-team`, the selected option and the `X-Team-Id` of the list call all agree after every switch, one page load per switch (no stray soft navigation).
- **Neighbours, same run:** `/team`, `/projects`, `/settings` switch both ways; `/ask` and `/notifications` reload the same page and remember the choice; a bookmark `/decisions` with team 3 remembered becomes `/decisions?team=3`; `/tasks?team=9` (not the person's) redirects with `?notice=team-gone`, shows "That team is no longer available to you, so you are back on your default team." and cleans the address; at 390 px the top-bar switcher behaves the same.
- **Tests:** `web/tests/team-switcher.test.tsx` mounts the switcher AND the sync together ("choosing a team while the sync is mounted", 8 tests; **all 8 fail with the guard removed**); `web/tests/active-team.test.ts` covers `planSwitch` and `planSync`. Lint and type-check run locally; CI runs the suite.

## Operate / roll back

Frontend only; deploy the web image as usual. Revert the PR to roll back.

## Follow-ups

- A remembered team that is gone, found on a team page with no `?team=`, clears the storage but showed no notice in the browser. The same on the code before this fix (checked), so not changed here; the unit test for it passes in jsdom. Worth a look.
- Not run against the real API in a browser (the mock replays the captured shapes).
