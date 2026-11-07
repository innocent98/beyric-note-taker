# SOP: UI parity batch 2, PR A (shell identity, navigation drawer, wording, Outlook row)

PR: `feature/ui-parity-batch-2a` (number in the PR list). From the 2026-11-07 audit (`docs/ui/audit-2026-11-07-app.md`, rows A2, A15, A24, A27, A28, A31 and the Calendar intro note).

## What shipped
1. **Navigation drawer for phones and tablets.** Lead-designed, not in the designer's file (owner's request 2026-11-07): the designer drew the desktop sidebar only (no `@media` in `Beyric Note Taker.dc.html`).
2. **Sidebar identity** (A2): the person's name, initials and email, as the design draws them (`D:38-42`), instead of the fixed "Owner" / "O".
3. **Wording to the file** (A15, A24, A27, A28).
4. **Settings > Calendar** (A31): a switched-off "Connect Microsoft Outlook" with one "Not available yet." line; the intro no longer names Microsoft Teams.

## Why
Phones showed the whole navigation stacked above the page; the sidebar named nobody; five strings differed from the file without a recorded decision; the file draws an Outlook button and the project rule is "drawn but no backend: shown off with one line".

## How: the decisions
| Topic | Decision |
|---|---|
| Breakpoint | The existing 860 px (`components.css`), so 768 px tablets use the drawer too; above it the designer's sidebar is untouched |
| Drawer | The same `<aside class="sidebar">` becomes `position: fixed`, off screen (`translateX(-100%)`, `visibility: hidden` so it cannot be tabbed into while closed), 300 px or 86 vw wide, over a scrim (`--scrim-drawer`, the token the task drawer uses). A 56 px sticky top bar holds a menu button and the logo; its height is fixed and the drawer is out of the flow, so nothing shifts |
| Behaviour | `web/components/app/shell-nav.tsx` (client). Open: `role="dialog"`, `aria-modal`, `aria-label="Main menu"` on the aside, `aria-expanded` and `aria-controls` on the button, focus moves to Close menu, Tab wraps inside, the page behind is `inert`, body scroll locked (scrollbar width kept as padding). Closes on Escape, scrim click, Close menu, any link click, a route change (state is "opened on path X", so a new path closes it with no effect) and when the window grows past 860 px. Focus returns to the menu button |
| Motion | 0.22 s slide and fade; none under `prefers-reduced-motion` |
| Touch | menu, close, nav links and Log out are 44 px high at this width |
| Icons and tokens | the app's own `Icon` set (one new line icon `menu`, three bars), `--shadow-drawer-left` (new, mirror of `--shadow-drawer`), no colour literals |
| Identity | `GET /api/session` already returns `user.name` and `user.email` (`UserOut`, `app/api_schemas.py`), so **no backend change**. `getShellUser()` (`web/lib/api/server.ts`) feeds `AppShell` from the session the page already read. No name: the part of the email before the `@`. Name 13 px / 550 and email 12 px grey, one line each with an ellipsis, avatar initials from `initials()` ("Adebayo Tosin" is "AT") |
| "None noted." | The string comes from the model prompt and `app/extraction.py` and `app/translation.py` compare against `NONE_NOTED` without a full stop. The safer change is display only: `fullStopNoneNoted()` in `notes-html.tsx` adds the stop to a part whose whole body is "None noted". Stored notes and the prompt are unchanged |
| Templates | rows "Untitled" / "No instructions yet" (also for the unsaved new row, which now shows the typed instructions), button always "Save" |
| Vocabulary | the count line is always drawn ("0 terms"); the invented empty state is gone as the file has none; the row button's tooltip is "Remove" (accessible name is "Remove {term}" so rows are told apart) |
| Outlook | disabled secondary button beside Google plus `Not available yet.`, only when not connected (the file draws it only then). Hidden from nobody; until Outlook M2 to M6 |

## What's involved
- New: `web/components/app/shell-nav.tsx`, `web/tests/shell-drawer.test.tsx`.
- Changed: `components/app/{shell,notes-html,meeting-view,templates-editor,vocabulary-manager,settings-calendar}.tsx`, `components/ui/{icon,icon-button}.tsx`, `lib/api/server.ts`, the twelve `app/**/page.tsx` that render `AppShell` (they pass `user`), `styles/{components,pages,tokens}.css`, tests that mock `@/lib/api/server` or `next/navigation`, `scripts/{parity-shots,mock-api}.mjs` (new shots; the mock's user has a name).
- No API, schema or migration change.

## Verification
- `npm run lint` (0 errors, 3 old warnings), `npm run typecheck`, `npx vitest run` in `web/`: green. New: `shell-drawer.test.tsx` (identity and its fallbacks, closed state, items kept, open as a modal dialog with focus and scroll lock, Escape, scrim, close button, link, route change, Tab wrap both ways, window growing past the breakpoint, scroll restored), `parity.test.tsx` (full stop), `settings-calendar.test.tsx` (Outlook off).
- Parity: `web/scripts/parity-shots.mjs` against `mock-api.mjs`, 1440 / 768 / 390, `--tz Africa/Lagos --now 2026-11-02T10:00:00Z`, in `docs/ui/parity/after/`: `app-nav-closed`, `app-nav-drawer` (no prototype, shot alone; at 1440 both show the unchanged desktop sidebar), `app-templates-new`, `app-templates`, `app-vocabulary`, `app-settings`, `app-meetings`. The React #418 printed for `/tasks` is the known harness clock difference.
- Not checked: a real phone or screen reader; the real backend (the mock stands in).

## Parity table
| Screen | Matches the design | Differences |
|---|---|---|
| Sidebar (1440) | logo, items, badges, user block with initials, name, email, Log out icon | an extra Notifications item (A3, existing); mock email is `owner@example.test` |
| Templates, new row | "Untitled", "No instructions yet", Save, Delete template | none |
| Vocabulary | "N terms" line (also "0 terms"), rows with title "Remove" | accessible name "Remove {term}" |
| Settings > Calendar | intro words; both Connect buttons side by side | Teams dropped from the intro (Teams links are refused); Outlook is off with "Not available yet." |
| Meeting page | "Rewriting notes..." while rewriting; "None noted." | the stop is added when drawn |
| Phone and tablet navigation | no design | lead-designed (above) |

## Operate / roll back
Server steps before merge: none. Revert the PR to roll back. The drawer needs JavaScript (the app already does); with it off the sidebar stays off screen on phones.

## Follow-ups
- A41 and A43 (Projects "· M recurring meetings") need the series-to-project link: a backend task.
- The designer should confirm or redraw the drawer.
- `docs/ui/parity-report.md` row "Sidebar name and email" is now done.
