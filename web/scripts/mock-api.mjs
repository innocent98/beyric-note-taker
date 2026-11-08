#!/usr/bin/env node
// A stand-in for FastAPI's JSON API, for looking at the owner screens without Docker, a database
// or a worker. It replays the captures in docs/fe-captures/owner-app/ as meetings 1 to 10, so every
// meeting state has a page. Nothing here is the real backend: writes answer with the capture shape
// and change nothing, and any email and password sign in.
//
//   node scripts/mock-api.mjs --port 8031
//   FASTAPI_INTERNAL_URL=http://127.0.0.1:8031 npm run dev        (or build, then start)
//
// Visit /login and sign in with any email and password. Start with --empty for the empty list, --stale for
// the worker banner.
//
// The accounts routes (sign-up, confirm email, reset password, waiting list, Google) replay
// docs/fe-captures/pilot-accounts/. Start with --pilot closed|open|full (default closed, which is what
// the real pilot is), --no-google to hide the Google button, --prefill you@example.test for a visitor
// the full pilot turned away, and --google-outcome ok|pilot_full|<code> (google_denied, google_failed, terms_required, ...) for where
// /api/auth/google/start sends the browser. Magic values, so every state can be reached by hand:
//   sign-up: password "password" -> 422 password_too_weak; email "rate@x.test" -> 429; invite "bad" -> 400
//   confirm: token "dead" -> 400 expired, token "full" -> 409 pilot_full, password "wrong" -> 400 mismatch
//   reset:   token "dead" -> 400 expired, password "password" -> 422 password_too_weak
//   sign-in: password "wrong" -> 401, email "locked@x.test" -> 429, email "gateway@x.test" -> a 429 page that is not JSON
import http from "node:http";
import { readFileSync, readdirSync } from "node:fs";
import { parseArgs } from "node:util";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const { values } = parseArgs({
  options: {
    port: { type: "string", default: "8031" },
    empty: { type: "boolean", default: false },
    stale: { type: "boolean", default: false },
    pilot: { type: "string", default: "closed" },
    "no-google": { type: "boolean", default: false },
    prefill: { type: "string" },
    "google-outcome": { type: "string", default: "google_denied" },
    calendar: { type: "string", default: "not_connected" },
    recurring: { type: "string", default: "series" },
    language: { type: "string", default: "en" },
    decisions: { type: "string", default: "live" },
    review: { type: "string", default: "live" },
    ask: { type: "string", default: "live" },
    "time-zone": { type: "string" },
    role: { type: "string", default: "owner" },
    overdue: { type: "boolean", default: false },
  },
});

// Team: `--role owner|admin|member` is the caller's role on the sample team (default owner). Invitation links:
// token "new" is for an address with no account, "existing" for owner@example.test, "onteam" is refused with
// 409 already_on_team, anything else answers 400 invite_invalid.
// Sprints (W9): the designer's own, Sprint 11 closed (Mon 26 Oct to Fri 30 Oct), Sprint 12 current (Mon 2 to
// Fri 6 Nov), Sprint 13 next, tasks filed as in the prototype. `GET /api/sprints`, Close sprint, the sprint fields
// of tasks, projects and `PATCH /api/team` behave as docs/fe-captures/sprints-w9/ shows (in memory); dates are
// fixed to the prototype's week, so run the page with a clock of 2026-11-02 (parity-shots `--now`).
// Decisions and Review: `--decisions live|loading|failed|empty|retry-loading` and `--review ...` (same words)
// pick what those lists answer (the captures in docs/fe-captures/ai-workspace-w1/; `loading` answers
// after 4 s, `failed` is a 500, `empty` has no item). Removing, checking and adding a term answer the
// captures too; the terms "Dup" and "Beyric" answer the captured 409 and "Gone" a 404 (the item's meeting deleted).
// Ask Beyric (spec docs/superpowers/specs/2026-11-08-designer-v2-features-design.md 1.7; the shapes are the
// spec's, since the real API was being built at the same time: the error envelope is the one the running API
// sends, `{"error": {"code", "message"}}`). `--ask live|empty|scope-failed`: `empty` is a person with no
// ready meetings (zero counts), `scope-failed` makes GET /api/ask/scope a 500. Questions reach every state
// by their words: "fail" is a 502 the first time (asking again answers), "limit" a 429 `ask_limit`, "rate" a 429 `ask_rate`,
// "zebra" the "none" answer, "tasks" or "commit" the tasks path, "gone" a chip to a meeting that is not
// there (999), "long" an answer with long chip labels, and "pricing" the full answer with transcript
// chips (meeting 8 has 20 rows; a teammate's chip names its owner).
// `--time-zone Africa/Lagos` starts the account with that zone chosen (without it GET
// /api/settings/time-zone answers UTC, not set); PUT keeps what it is given, in memory.

// `--overdue` puts two open tasks and two of the Series page's unfinished tasks on Fri 30 Oct, before the
// mock's Mon 2 Nov, as the designer's sample does ("Overdue, Fri 30 Oct": docs/ui/audit-2026-11-07-main-v2.md B10, B11).
// Translation (W7): `--language fr|es|en` is the account's default notes language to start with.
// Meeting 8 is the designer's sample meeting ("Zoom 88205679674", the notes and transcript of
// docs/fe-captures/translate/); it is not in the list. Asking for French or Spanish on it answers
// the captured 202, then the captured translation 1.5 seconds later. Nothing is translated here:
// the French and Spanish are the designer's own words, as captured with the model faked.

// `--calendar not_connected|connected|needs_reconnect|off`: what GET /api/calendar answers (the captures in
// docs/fe-captures/calendar-state/; `paused` is connected with auto-join stopped by the operator; `off` is the feature being dark: 404 calendar_unavailable).
// GET /api/calendar/recurring follows the same flag: `connected` replays the captures in
// docs/fe-captures/calendar-recurring/ (by `?tz=`: Africa/Lagos, Pacific/Auckland, else UTC), the
// other two answer the empty list and the 404 of those captures. `--recurring none` forces the
// empty list while connected, `--recurring two` keeps the two weekly series (the designer's sample).
const captureDir = join(dirname(fileURLToPath(import.meta.url)), "../../docs/fe-captures");
const captures = join(captureDir, "owner-app");
const body = (name) => JSON.parse(readFileSync(join(captures, `${name}.json`), "utf8")).response.body;
const prelaunchBody = (name) => JSON.parse(readFileSync(join(captureDir, "prelaunch-mail-prefs", `${name}.json`), "utf8")).response.body;
const calendarCapture = (name) => JSON.parse(readFileSync(join(captureDir, "calendar-state", `${name}.json`), "utf8")).response.body;
const recurringCapture = (name) => JSON.parse(readFileSync(join(captureDir, "calendar-recurring", `${name}.json`), "utf8")).response.body;
const settingsCapture = (name) => JSON.parse(readFileSync(join(captureDir, "calendar-settings", `${name}.json`), "utf8")).response.body;
const accountBody = (name) => JSON.parse(readFileSync(join(captureDir, "pilot-accounts", `${name}.json`), "utf8")).response.body;

// One meeting per state, from the captures. Titles vary so the list reads like a real one.
const details = [
  ["47-meeting-detail-ready", "Beyric Monday planning"],
  ["43-meeting-detail-processing", "Client call with Acme"],
  ["35-meeting-detail-bot-sent", "Google Meet kqz-mnwp-tuv"],
  ["49-meeting-detail-failed-retryable", "Design review"],
  ["50-meeting-detail-failed-no-speech", "Team stand-up"],
  ["48-meeting-detail-ready-removed", "Interview, backend role"],
  ["41-meeting-detail-zoom-second-attempt", "Zoom 842 1190 4471"],
].map(([file, title], index) => {
  const meeting = body(file);
  const age = new Date(Date.UTC(2026, 10, 2, 9 + index, 30)).toISOString();
  return { ...meeting, id: index + 1, title, created_at: age, translations: [] };
});
// The owner-app captures also predate the detail's duration, participants and the two counts of
// the "Decisions and review" card. These are the mock's own values, for looking at the page (the
// designer's sample: a 46 minute meeting of Adebayo, Daniel Okoro and Amara Nwosu).
for (const meeting of details) {
  const ready = meeting.status === "ready";
  meeting.duration_seconds = ready ? 2760 : null;
  meeting.participants = ready ? ["Adebayo", "Daniel Okoro", "Amara Nwosu"] : [];
  meeting.decisions_count = ready ? 2 : 0;
  meeting.review_open_count = ready ? 3 : 0;
  meeting.can_delete = meeting.status !== "bot_sent" && meeting.status !== "processing";
  if (ready) meeting.extraction_status = "done";
  Object.assign(meeting, { shared_with_team: false, can_share: true, shared_by: null });
}
// The owner-app captures predate `template_name`; the running API sends it on every meeting.
for (const meeting of details) meeting.template_name = body("11-templates-seeded").templates.find((t) => t.id === meeting.template_id)?.name ?? "General";
const summary = ({ id, title, platform, native_id, status, step, status_text, status_tone, template_id, template_name, created_at }) => ({
  id, title, platform, native_id, status, step, status_text, status_tone, template_id, template_name, created_at,
  tasks_count: meetingTasks(id).length,
});


// Team (W3). The shapes are the ones in docs/fe-captures/team-w3/; the people are the designer's
// sample team (Adebayo Tosin owner, Daniel Okoro admin, Amara Nwosu and Ojebola Grace members, Kemi
// Adeyemi invited). `--role member` shows the screen as a member sees it. Writes change it in memory.
const teamRole = values.role ?? "owner";
const team = { id: 1, name: "My team", share_new_meetings: false, reminders_on: false, reminder_timing: "both", sprint_length: "week5", sprint_custom_days: 10, sprint_align_weekdays: true };
const teamMembers = [
  { user_id: 2, name: "Adebayo Tosin", email: "adebayo@beyrictech.com", role: "owner", aliases: ["Tosin", "Adebayo", "s12Adebayo"], open_tasks: 3 },
  { user_id: 3, name: "Daniel Okoro", email: "daniel@beyrictech.com", role: "admin", aliases: ["Daniel", "Dan"], open_tasks: 2 },
  { user_id: 4, name: "Amara Nwosu", email: "amara@beyrictech.com", role: "member", aliases: ["Amara"], open_tasks: 3 },
  { user_id: 5, name: "Ojebola Grace", email: "grace.ojebola@beyrictech.com", role: "member", aliases: ["Grace"], open_tasks: 1 },
];
const teamInvites = [
  { id: 1, email: "kemi.adeyemi@gmail.com", name: null, role: "member", status: "pending", invited_by: "Adebayo Tosin", last_sent_at: "2026-11-02T09:00:00Z", expires_at: "2026-11-09T09:00:00Z" },
];
const myTeamId = () => (teamRole === "owner" ? 2 : teamRole === "admin" ? 3 : 4);
const manager = () => teamRole !== "member";
const teamOut = () => ({ ...team, my_role: teamRole, member_count: teamMembers.length, pending_invites: teamInvites.length, can_manage: manager(), can_change_roles: teamRole === "owner" });
const membersOut = () => ({
  members: teamMembers.map((m) => ({
    ...m, is_you: m.user_id === myTeamId(), joined_at: "2026-11-01T09:00:00Z",
    can_change_role: teamRole === "owner" && m.role !== "owner",
    can_edit_aliases: manager() || m.user_id === myTeamId(),
    can_remove: m.user_id === myTeamId() ? m.role !== "owner" : m.role === "owner" ? false : teamRole === "owner" || (teamRole === "admin" && m.role === "member"),
  })),
  invites: teamInvites,
});
const forbidden = { error: { code: "team_forbidden", message: "Only an owner or admin of the team can do that." } };

// Tasks (W2). The shape is the one in docs/fe-captures/tasks-w2/ (`can_*`, `assignee`, `meeting`...);
// the values are the mock's own: the designer's sample tasks (Beyric Monday planning, 2 Nov), so a
// screen can be set next to the prototype. Writes change them in memory. Meeting 1 carries the first
// eight, as in the design; the others point at meetings that are not in the list.
const me = { user_id: 2, name: "Adebayo Tosin", is_you: true };
const people = [me, { user_id: 3, name: "Daniel Okoro", is_you: false }, { user_id: 4, name: "Amara Nwosu", is_you: false }, { user_id: 5, name: "Ojebola Grace", is_you: false }];
const personOf = (id) => people.find((p) => p.user_id === id) ?? null;
const refOf = (id, title, day) => ({ id, title, platform: "zoom", created_at: `${day}T09:30:00Z` });
const mondayRef = refOf(1, "Beyric Monday planning", "2026-11-02");
const fridayRef = refOf(11, "Friday review", "2026-10-30");
const planningRef = refOf(12, "Monday planning", "2026-10-26");
const allowed = { can_update: true, can_edit: true, can_assign: true, can_claim: false, can_delete: true };
let nextTaskId = 100;
const makeTask = (id, title, who, due, status, ref, extra = {}) => ({
  id, title, status, assignee: personOf(who), spoken_owner: null, due_date: due, due_phrase: null,
  quote: null, is_new: !!ref && ref === mondayRef && status !== "done", unverified: false, source: ref ? "ai" : "manual",
  assigned_on: ref ? ref.created_at.slice(0, 10) : "2026-11-02", done_at: status === "done" ? "2026-11-02T10:00:00Z" : null,
  created_at: "2026-11-02T10:00:00Z", from_meeting: !!ref, meeting: ref, project_id: ref ? 1 : null, milestone_id: null, check_project: false, ...allowed, ...extra,
});
const tasks = [
  makeTask(8, "Continue hiring for the customer success lead role", null, "2026-11-06", "todo", mondayRef, { spoken_owner: "Kemi Adeyemi", quote: "Intended start on or before Friday.", can_claim: true }),
  makeTask(7, "Share the meeting summary with the wider team", 2, null, "todo", mondayRef, { quote: "No due date was stated." }),
  makeTask(6, "Support Daniel Okoro with testing during the week", 2, "2026-11-06", "todo", mondayRef),
  makeTask(5, "Finish payment checkout", 2, "2026-11-06", "todo", mondayRef, { quote: "Aim to have it ready before the end of the week." }),
  makeTask(4, "Organise the Figma file and move the design system into the team workspace", 4, null, "todo", mondayRef, { quote: "Timing was described as the coming days or weeks." }),
  makeTask(3, "Specify and log planned design refinements and empty states on the tracker", 4, null, "todo", mondayRef),
  makeTask(2, "Post this week's specific tasks in Slack after testing", 3, "2026-11-02", "todo", mondayRef, { quote: "By the end of today." }),
  makeTask(1, "Finish checking web and mobile data sync, and test both apps", 3, "2026-11-02", "todo", mondayRef, { quote: "Said he could finish the test this evening." }),
  makeTask(9, "Set up Slack channels for the product team", 3, "2026-11-04", "doing", fridayRef),
  makeTask(10, "Refine phase one onboarding copy", 4, "2026-11-05", "doing", fridayRef),
  makeTask(11, "Implement role-based permissions", 2, null, "done", planningRef),
  makeTask(12, "Update onboarding screens", 3, null, "done", planningRef),
];
// Projects and milestones (W4). The shapes are the ones in docs/fe-captures/projects-w4/; the values
// are the designer's: Client Portal (teal) and Beyric Note Taker (blue), the milestone "Client Portal
// launch, Tue 8 Dec 2026" set in the Monday planning, eight launch tasks and one task to check. The
// designer's recurring-meeting links are the designer's own (Client Portal has both recurring meetings,
// Beyric Note Taker none): `PUT /api/series/{id}/project` changes them in memory, the answers have the
// shapes in docs/fe-captures/series-project-link/.
// `--role member` shows the screens as a member sees them. Writes change them in memory.
let proposalOpen = true; // meeting 8 carries one unconfirmed proposal until it is confirmed
let projects = [];
let seriesProject = {}; // series id -> project id (the link `PUT /api/series/{id}/project` writes)
let milestones = [];
let nextProjectId = 10;
function resetProjects() {
  seriesProject = { 1: 1, 3: 1 };
  projects = [
    { id: 1, name: "Client Portal", color: "#0E6B5E", keywords: ["portal", "onboarding", "checkout", "pricing"], created_at: "2026-11-01T09:00:00Z" },
    { id: 2, name: "Beyric Note Taker", color: "#3C4A7A", keywords: ["Beyric", "note taker", "transcript"], created_at: "2026-11-01T09:01:00Z" },
  ];
  milestones = [{ id: 1, name: "Client Portal launch", due_date: "2026-12-08", project_id: 1, source_meeting: mondayRef, quote: "Launch is December 8.", confirmed_at: "2026-11-02T11:00:00Z", task_count: 0, done_count: 0 }];
}
resetProjects();
const zoomRef = refOf(8, "Zoom 88205679674", "2026-11-02");
tasks.push(
  makeTask(13, "Measure transcript accuracy against the September 12 meeting", 5, "2026-11-05", "todo", zoomRef, { project_id: 2, is_new: false }),
  makeTask(14, "Decide how sending the note taker can be automated", 2, null, "todo", zoomRef, { project_id: 2, is_new: false }),
);
for (const t of tasks) if ([1, 3, 5, 8, 9, 10, 11, 12].includes(t.id)) t.milestone_id = 1;
tasks.find((t) => t.id === 6).check_project = true;
const proposal = { id: 2, name: "Beta release", due_date: "2026-11-20", project_id: 2, source_meeting: refOf(8, "Zoom 88205679674", "2026-11-02"), quote: "We want the beta out by the twentieth of November.", confirmed_at: null, task_count: 0, done_count: 0 };

// Sprints (W9). The shapes are the ones in docs/fe-captures/sprints-w9/; the values are the designer's:
// Sprint 11 closed, Sprint 12 current (Mon 2 to Fri 6 Nov 2026), Sprint 13 next. A series is the team's, or a
// project's own when it has a sprint length of its own; its ids are the sprint number (team) or 1000 x project id
// plus the number (own). Dates follow the designer's `spInfo` from the number, so they never drift.
const TODAY = "2026-11-02";
const LENGTHS = { week5: { days: 5, step: 7 }, week7: { days: 7, step: 7 }, week14: { days: 14, step: 14 }, month1: { days: 30, step: 30 }, month3: { months: 3 }, month6: { months: 6 } };
const dayIso = (d) => d.toISOString().slice(0, 10);
const plusDays = (day, n) => dayIso(new Date(Date.parse(`${day}T00:00:00Z`) + n * 864e5));
const plusMonths = (day, n) => {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCMonth(d.getUTCMonth() + n);
  return dayIso(d);
};
let seriesList = [];
function resetSprints() {
  team.sprint_length = "week5";
  team.sprint_custom_days = 10;
  team.sprint_align_weekdays = true;
  seriesList = [{ pid: null, base: 12, current: 12, closed: [{ number: 11, early: false }] }];
  for (const t of tasks) {
    t.sprint_id = { 1: 12, 2: 12, 5: 12, 6: 12, 8: 12, 9: 12, 10: 12, 11: 11, 12: 11, 4: 13, 13: 12 }[t.id] ?? null;
    t.carried = [9, 10].includes(t.id);
  }
  for (const p of projects) p.sprint_length = null;
}
const lengthOf = (pid) => (pid === null ? team.sprint_length : (projects.find((p) => p.id === pid)?.sprint_length ?? team.sprint_length));
const sprintId = (series, number) => (series.pid === null ? number : series.pid * 1000 + number);
function windowOf(series, number) {
  const length = lengthOf(series.pid);
  const days = length === "custom" ? team.sprint_custom_days : LENGTHS[length].days;
  const step = length === "custom" ? team.sprint_custom_days : LENGTHS[length].step;
  const offset = number - series.base;
  if (LENGTHS[length]?.months) {
    const start = plusMonths(TODAY, offset * LENGTHS[length].months);
    return { start_date: start, end_date: plusDays(plusMonths(start, LENGTHS[length].months), -1) };
  }
  const start = plusDays(TODAY, offset * step);
  return { start_date: start, end_date: plusDays(start, days - 1) };
}
const seriesOf = (pid) => {
  const owned = pid !== null && projects.find((p) => p.id === pid)?.sprint_length;
  const want = owned ? pid : null;
  let found = seriesList.find((x) => x.pid === want);
  if (!found) seriesList.push((found = { pid: want, base: 1, current: 1, closed: [] }));
  return found;
};
const roleIds = (role) => seriesList.map((x) => sprintId(x, role === "current" ? x.current : x.current + 1));
function sprintOut(series, number, state, project) {
  const id = sprintId(series, number);
  const closed = series.closed.find((c) => c.number === number);
  const window = windowOf(series, number);
  const ids = state === "closed" || series.pid !== null ? [id] : roleIds(state);
  const mine = tasks.filter((t) => ids.includes(t.sprint_id) && (project === undefined || project === null || t.project_id === project));
  return {
    id, number, name: `Sprint ${number}`, ...window, state,
    closed_at: closed ? "2026-10-30T17:00:00Z" : null, closed_early: closed?.early ?? false,
    tasks_total: mine.length, tasks_done: mine.filter((t) => t.status === "done").length,
  };
}
function sprintList(pid) {
  const series = seriesOf(pid);
  const counting = pid !== null && series.pid === null ? pid : undefined;
  const sprints = [
    ...series.closed.slice(-20).map((c) => sprintOut(series, c.number, "closed", counting)),
    sprintOut(series, series.current, "current", counting),
    sprintOut(series, series.current + 1, "next", counting),
  ];
  return {
    project_id: pid, own_series: series.pid !== null, today: TODAY, sprint_length: lengthOf(series.pid),
    sprint_custom_days: team.sprint_custom_days, sprint_align_weekdays: team.sprint_align_weekdays,
    sprints, current_id: sprintId(series, series.current), next_id: sprintId(series, series.current + 1), can_manage: manager(),
  };
}
const notOnTeam = { error: { code: "sprint_not_on_team", message: "That sprint is not available for this task." } };
// A task's own series' open sprints (what its select offers), and where a due date files it.
const openIdsFor = (pid) => {
  const series = seriesOf(pid);
  return [sprintId(series, series.current), sprintId(series, series.current + 1)];
};
const fileByDue = (due, pid) => {
  if (!due) return null;
  const series = seriesOf(pid);
  return due <= windowOf(series, series.current).end_date ? sprintId(series, series.current) : sprintId(series, series.current + 1);
};
// A task that changes project keeps its place in time (current stays current, next stays next).
function rehome(task, fromPid, toPid) {
  const from = seriesOf(fromPid);
  const to = seriesOf(toPid);
  if (from === to) return;
  if (task.sprint_id === sprintId(from, from.current)) task.sprint_id = sprintId(to, to.current);
  else if (task.sprint_id === sprintId(from, from.current + 1)) task.sprint_id = sprintId(to, to.current + 1);
}
resetSprints();
const projectOut = (p) => ({
  ...p,
  open_tasks: tasks.filter((t) => t.project_id === p.id && t.status !== "done").length,
  sprint_length: p.sprint_length ?? null,
  in_current_sprint: tasks.filter((t) => t.project_id === p.id && t.status !== "done" && t.sprint_id === openIdsFor(p.id)[0]).length,
  in_backlog: tasks.filter((t) => t.project_id === p.id && t.status !== "done" && t.sprint_id === null).length,
  recurring_meetings: Object.values(seriesProject).filter((id) => id === p.id).length,
  milestone: (() => {
    const m = milestones.filter((x) => x.project_id === p.id && x.confirmed_at).sort((a, b) => (a.due_date < b.due_date ? -1 : 1))[0];
    return m ? { id: m.id, name: m.name, due_date: m.due_date } : null;
  })(),
});
const milestoneOut = (m) => {
  const ofIt = tasks.filter((t) => t.milestone_id === m.id);
  const manage = teamRole !== "member";
  return { ...m, task_count: ofIt.length, done_count: ofIt.filter((t) => t.status === "done").length, can_confirm: manage && !m.confirmed_at, can_edit: manage };
};
const meetingTasks = (id) => tasks.filter((t) => t.meeting?.id === id).sort((a, b) => b.id - a.id);
const tasksNew = () => tasks.filter((t) => t.is_new && t.status !== "done").length;
const attention = () => ({ needs_owner: tasks.filter((t) => !t.assignee && t.status !== "done").length, unverified: 0, check_project: tasks.filter((t) => t.check_project && t.status !== "done").length });
let tasksSettings = { auto_create_tasks: true, email_assignees: true };
const integrationsCapture = (name) => JSON.parse(readFileSync(join(captureDir, "integrations-w10", `${name}.json`), "utf8")).response.body;
let slackConnected = false;
let linearConnected = false;
for (const meeting of details) {
  meeting.tasks = [];
  meeting.tasks_count = 0;
}

// The templates are in memory so that "Delete template" can be tried by hand (a delete is replayed from
// docs/fe-captures/template-delete/: 204, the 409 for the last one, the 404 for one that is gone).
const translateCapture = (name) => JSON.parse(readFileSync(join(captureDir, "translate", `${name}.json`), "utf8")).response.body;
const sample = {
  ...translateCapture("05-meeting-before"),
  id: 8,
  can_delete: true,
  title: "Zoom 88205679674",
  platform: "zoom",
  native_id: "88205679674",
  template_name: body("11-templates-seeded").templates.find((t) => t.id === translateCapture("05-meeting-before").template_id)?.name ?? "General",
  created_at: "2026-11-02T11:48:00Z",
  duration_seconds: 180,
  participants: ["Adebayo", "Ojebola Grace"],
  decisions_count: 2,
  review_open_count: 0,
  extraction_status: "done",
  tasks: [],
  tasks_count: 0,
  shared_with_team: false,
  can_share: true,
  shared_by: null,
};
// Meeting 9 is the sample with a participant who is not on the team (the lead-designed "Add email").
const withStranger = { ...sample, id: 9, title: "Zoom 99205679674", native_id: "99205679674", participants: ["Adebayo", "Kemi Adeyemi"] };
const shareCapture = (name) => JSON.parse(readFileSync(join(captureDir, "share", `${name}.json`), "utf8")).response.body;
let shares = []; // newest first
let shareId = 0;
const translating = {}; // language -> when it was asked for
const TRANSLATE_AFTER_MS = 1500;
const translationOf = (language) => {
  const since = translating[language];
  if (since === undefined) return null;
  if (Date.now() - since < TRANSLATE_AFTER_MS) return { ...translateCapture("07-translate-fr-queued"), language };
  return translateCapture(language === "fr" ? "10-translate-fr-ready" : "14-translate-es-ready");
};
let defaultLanguage = values.language;
const noTranslation = { error: { code: "no_translation", message: "This meeting has no translation yet." } };

const templates = body("11-templates-seeded").templates;
const deleteCapture = (name) => JSON.parse(readFileSync(join(captureDir, "template-delete", `${name}.json`), "utf8")).response.body;
const terms = body("19-vocabulary-list").terms;

function json(res, status, payload, headers = {}) {
  res.writeHead(status, { "content-type": "application/json", "cache-control": "no-store", ...headers });
  res.end(JSON.stringify(payload));
}

let notifyEmail = true;
let timeZone = values["time-zone"] ?? null;
const mockUser = () => ({ email: "owner@example.test", name: "Adebayo Tosin", is_admin: false, email_verified: true, time_zone: timeZone });
const wsCapture = (name) => JSON.parse(readFileSync(join(captureDir, "ai-workspace-w1", `${name}.json`), "utf8")).response.body;
const armed = { decisions: true, review: true, ask: true }; // `retry-loading` fails once after each reset
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const readJson = (req) =>
  new Promise((resolve) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8") || "null"));
      } catch {
        resolve(null);
      }
    });
  });

// The accounts routes. Each answer is the body of a capture from the running app (the file is named
// in the comment), so a screen built here sees the shapes the real API sends.
function accounts(path, method, data, res) {
  const email = String(data?.email ?? "");
  if (path === "/api/pilot" && method === "GET") {
    return json(res, 200, {
      state: values.pilot, // 04 closed, 11 open, 57 full
      prefill_email: values.prefill ?? null, // 122
      google_signin: !values["no-google"], // 02 is the same shape with false
    });
  }
  if (path === "/api/signup" && method === "POST") {
    if (email.startsWith("rate@")) return json(res, 429, accountBody("124-signup-rate-limited"));
    if (data?.invite_token === "bad") return json(res, 400, accountBody("82-signup-with-invite-wrong-address"));
    if (!data?.invite_token && values.pilot === "closed") return json(res, 403, accountBody("05-signup-while-closed"));
    if (!data?.invite_token && values.pilot === "full") return json(res, 409, accountBody("59-signup-pilot-full"));
    if (data?.accept_terms !== true) return json(res, 422, accountBody("13-signup-terms-not-accepted"));
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return json(res, 422, accountBody("14-signup-bad-email"));
    if (data?.password === "password") return json(res, 422, accountBody("12-signup-weak-password"));
    return json(res, 202, accountBody("18-signup-ok"));
  }
  if (path === "/api/signup/resend" && method === "POST") {
    if (email.startsWith("rate@")) return json(res, 429, accountBody("124-signup-rate-limited"));
    return json(res, 202, accountBody("35-resend-unverified"));
  }
  if (path === "/api/verify-email" && method === "POST") {
    if (data?.token === "dead") return json(res, 400, accountBody("23-verify-unknown-token"));
    if (data?.token === "full") return json(res, 409, accountBody("58-verify-pilot-filled-up"));
    if (data?.password === "wrong") return json(res, 400, accountBody("22-verify-wrong-password"));
    return json(res, 200, accountBody("24-verify-ok"));
  }
  if (path === "/api/password/forgot" && method === "POST") {
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return json(res, 422, accountBody("14-signup-bad-email"));
    if (email.startsWith("rate@")) return json(res, 429, accountBody("124-signup-rate-limited"));
    return json(res, 202, accountBody("39-forgot-known"));
  }
  if (path === "/api/password/reset" && method === "POST") {
    if (data?.token === "dead") return json(res, 400, accountBody("43-reset-bad-token"));
    if (data?.password === "password") return json(res, 422, accountBody("42-reset-weak-password"));
    return json(res, 200, accountBody("44-reset-ok"));
  }
  if (path === "/api/waitlist" && method === "POST") {
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return json(res, 422, accountBody("51-waitlist-bad-email"));
    return json(res, 202, accountBody("49-waitlist-join"));
  }
  return null;
}

// Series (W8). The bodies are the captures in docs/fe-captures/series-w8/ (the owner's view): the
// Recurring cards, the Series page of "Beyric Monday planning" (1) and "Beyric Friday review" (3).
// Agenda lines, resolved questions and the pills change in memory; "Send agenda" answers the capture.
// Meeting 1 is a meeting of series 1 and carries the captured open questions.
const seriesDir = join(captureDir, "series-w8");
const seriesCapture = (n) => {
  const file = readdirSync(seriesDir).find((f) => f.startsWith(`${n}-`));
  return JSON.parse(readFileSync(join(seriesDir, file), "utf8")).response.body;
};
const seriesPages = { 1: seriesCapture("11"), 3: seriesCapture("14") };
// Captures 08, 11 and 14 are older than the link and the sprint line: `project` is added from the in-memory
// links (see "Projects"), and Monday planning's next meeting (Mon 9 Nov) is the day Sprint 13 starts in
// the sprints mock, so the real rule gives "Opens Sprint 13"; Friday review has no line (no rule for "Closes").
seriesPages[1].sprint_note = "Opens Sprint 13";
seriesPages[3].sprint_note = null;
const projectLink = (id) => {
  const project = projects.find((p) => p.id === seriesProject[id]);
  return project ? { id: project.id, name: project.name } : null;
};
const seriesCards = () => {
  const body = seriesCapture("08");
  return { ...body, series: body.series.map((card) => ({ ...card, project: projectLink(card.id) })) };
};
if (values.overdue) {
  seriesPages[1].unfinished.slice(0, 2).forEach((t) => (t.due_date = "2026-10-30"));
  [tasks.find((t) => t.id === 5), tasks.find((t) => t.id === 6)].forEach((t) => (t.due_date = "2026-10-30"));
}
let nextAgendaId = 100;
const questionState = Object.fromEntries(seriesCapture("15").open_questions.map((q) => [q.id, { ...q }]));
details[0].series = { id: 1, title: "Beyric Monday planning" };
details[0].open_questions = Object.values(questionState);
details[0].notes_html =
  "<h2>Summary</h2><p>The team reviewed Client Portal progress ahead of the launch.</p><h2>Decisions</h2><ul><li>Use Slack for work updates.</li></ul><h2>Action items</h2><ul><li>Dan to finish payment checkout by Friday.</li></ul><h2>Open questions</h2><ul>" +
  Object.values(questionState).map((q) => `<li>${q.text}</li>`).join("") +
  "</ul>";

// Lead-designed pieces (W8, approved 2026-11-06): meeting 6 reuses a link, so it suggests "Make this a
// recurring meeting?"; "Add to recurring meeting" and "Make recurring" answer in memory; the
// person's own reminder mail switch is GET/PUT /api/settings/reminders.
details[5].suggest_recurring = true;
let notifyReminders = true;

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://x");
  const path = url.pathname.replace(/\/$/, "");
  const loggedIn = /(^|;\s*)session=owner/.test(req.headers.cookie ?? "");
  const method = req.method ?? "GET";
  const data = ["POST", "PUT", "PATCH"].includes(method) ? await readJson(req) : (req.resume(), null);

  // Not part of the API: `/__mock/set?decisions=failed&review=empty` changes the Decisions and Review
  // states while the mock runs (the Next build proxies the browser's calls to one address, so one mock
  // has to serve every state).
  if (path === "/__mock/set") {
    for (const key of ["decisions", "review", "ask"]) if (url.searchParams.has(key)) values[key] = url.searchParams.get(key);
    return json(res, 200, { decisions: values.decisions, review: values.review, ask: values.ask });
  }
  // Not part of the API: lets parity-shots.mjs start each translation screen from nothing.
  if (path === "/__mock/reset") {
    for (const language of Object.keys(translating)) delete translating[language];
    defaultLanguage = values.language;
    shares = [];
    shareId = 0;
    armed.decisions = armed.review = armed.ask = true;
    slackConnected = false;
    resetProjects();
    resetSprints();
    for (const t of tasks) t.milestone_id = [1, 3, 5, 8, 9, 10, 11, 12].includes(t.id) ? 1 : null;
    proposalOpen = true;
    linearConnected = false;
    return json(res, 200, { reset: true });
  }

  // The real API refuses a write without the token from GET /api/session, public routes included.
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method) && path.startsWith("/api/") && req.headers["x-csrf-token"] !== "mock-csrf") {
    return json(res, 403, accountBody("17-signup-without-csrf"));
  }

  if (path === "/api/auth/google/start" && method === "GET") {
    if (values["no-google"]) return json(res, 404, accountBody("03-google-start-unavailable"));
    const outcome = values["google-outcome"];
    if (outcome === "ok") {
      res.writeHead(302, { location: "/", "set-cookie": "session=owner; Path=/; HttpOnly; SameSite=Lax" });
    } else if (outcome === "pilot_full") {
      res.writeHead(302, { location: "/pilot-full" });
    } else {
      res.writeHead(302, { location: `/login?error=${outcome}` });
    }
    return res.end();
  }
  const account = accounts(path, method, data, res);
  if (account !== null) return account;

  if (path === "/api/session") {
    if (method === "POST") {
      if (String(data?.email ?? "").startsWith("locked@")) return json(res, 429, body("80-limit-sixth-attempt"));
      if (String(data?.email ?? "").startsWith("gateway@")) {
        res.writeHead(429, { "content-type": "text/html" });
        return res.end("<html><body><h1>429 Too Many Requests</h1><hr>nginx</body></html>");
      }
      if (data?.password === "wrong") return json(res, 401, accountBody("21-login-before-verifying"));
      return json(res, 200, { authenticated: true, csrf_token: "mock-csrf", user: mockUser(), usage: { used_seconds: 0, cap_seconds: 18000 } }, { "set-cookie": "session=owner; Path=/; HttpOnly; SameSite=Lax" });
    }
    if (method === "DELETE") {
      return json(res, 200, { authenticated: false, csrf_token: "mock-csrf", user: null, usage: null }, { "set-cookie": "session=; Path=/; Max-Age=0" });
    }
    return json(res, 200, loggedIn ? { authenticated: true, csrf_token: "mock-csrf", user: mockUser(), usage: { used_seconds: 0, cap_seconds: 18000 }, unread_notifications: 1, badges: { review_open: 0, tasks_new: tasksNew() } } : { authenticated: false, csrf_token: "mock-csrf", user: null, usage: null });
  }
  // The invitation link: public preview, then (signed in) accept.
  if (path === "/api/team/invites/preview" && method === "GET") {
    const token = url.searchParams.get("token");
    if (!["new", "existing", "onteam"].includes(token)) return json(res, 400, { error: { code: "invite_invalid", message: "That invitation is not valid any more. Ask for a new one." } });
    return json(res, 200, { email: token === "new" ? "kemi.adeyemi@gmail.com" : "owner@example.test", name: token === "new" ? "Kemi" : null, role: "member", team_name: "Product team", inviter_name: "Adebayo Tosin", has_account: token !== "new", expires_at: "2026-11-09T09:00:00Z" });
  }
  if (path.startsWith("/api/") && !loggedIn) {
    return json(res, 401, { error: { code: "not_authenticated", message: "Log in to continue." } });
  }
  if (path === "/api/meetings" && method === "GET") {
    const meetings = values.empty ? [] : details.map(summary).reverse();
    return json(res, 200, { meetings, has_more: false, next_before_id: null, worker_stale: values.stale || values.empty });
  }
  if (path === "/api/team/invites/accept" && method === "POST") {
    if (data?.token === "onteam") return json(res, 409, { error: { code: "already_on_team", message: "You are on another team. Leave it first, then accept this invitation." } });
    if (data?.token !== "existing") return json(res, 400, { error: { code: "invite_invalid", message: "That invitation is not valid any more. Ask for a new one." } });
    return json(res, 200, teamOut());
  }
  if (path === "/api/settings/reminders") {
    if (method === "PUT" && typeof data?.notify_reminders === "boolean") notifyReminders = data.notify_reminders;
    return json(res, 200, { notify_reminders: notifyReminders });
  }
  const setSeries = /^\/api\/meetings\/(\d+)\/(series|recurring)$/.exec(path);
  if (setSeries && ["PUT", "POST"].includes(method)) {
    const meeting = details.find((m) => m.id === Number(setSeries[1]));
    if (!meeting) return json(res, 404, { error: { code: "not_found", message: "No such meeting" } });
    if (setSeries[2] === "recurring") {
      meeting.series = { id: 9, title: meeting.title };
      meeting.suggest_recurring = false;
      return json(res, 201, { series: meeting.series });
    }
    meeting.series = data?.series_id ? { id: data.series_id, title: "Beyric Monday planning" } : null;
    return json(res, 200, { series: meeting.series });
  }
  if (path === "/api/series" && method === "POST") {
    return json(res, 201, { id: 9, title: String(data?.title ?? ""), cadence: "Recurring", platform: null, next_starts_at: null, next_text: null, unfinished_tasks: 0, open_questions: 0, project: null });
  }
  // Series (W8): see the block above the server.
  if (path === "/api/series" && method === "GET") return json(res, 200, seriesCards());
  const linking = /^\/api\/series\/(\d+)\/project$/.exec(path);
  if (linking && method === "PUT") {
    const card = seriesCards().series.find((c) => c.id === Number(linking[1]));
    if (!card) return json(res, 404, { error: { code: "not_found", message: "No such recurring meeting" } });
    if (teamRole === "member") return json(res, 403, { error: { code: "team_forbidden", message: "You can't do that here. Ask an owner or admin of your team." } });
    if (data?.project_id === null) delete seriesProject[card.id];
    else if (projects.some((p) => p.id === data?.project_id)) seriesProject[card.id] = data.project_id;
    else return json(res, 404, { error: { code: "not_found", message: "No such project." } });
    return json(res, 200, seriesCards().series.find((c) => c.id === card.id));
  }
  const onePage = /^\/api\/series\/(\d+)$/.exec(path);
  if (onePage && method === "GET") {
    const page = seriesPages[onePage[1]];
    return page ? json(res, 200, page) : json(res, 404, { error: { code: "not_found", message: "No such recurring meeting" } });
  }
  const agenda = /^\/api\/series\/(\d+)\/agenda(?:\/(\d+)|\/send)?$/.exec(path);
  if (agenda && seriesPages[agenda[1]]) {
    const page = seriesPages[agenda[1]];
    if (path.endsWith("/send") && method === "POST") return json(res, 200, seriesCapture("35"));
    if (method === "POST") {
      const text = String(data?.text ?? "").trim();
      if (/https?:\/\/|www\./i.test(text)) return json(res, 422, { error: { code: "invalid_agenda_item", message: "Write the item without a web address." } });
      const item = { id: nextAgendaId++, text, can_remove: true };
      page.agenda.push(item);
      return json(res, 201, item);
    }
    if (method === "DELETE" && agenda[2]) {
      page.agenda = page.agenda.filter((i) => i.id !== Number(agenda[2]));
      res.writeHead(204);
      return res.end();
    }
  }
  const question = /^\/api\/questions\/(\d+)$/.exec(path);
  if (question && method === "PATCH") {
    const item = questionState[question[1]];
    if (!item) return json(res, 404, { error: { code: "not_found", message: "No such question" } });
    if (typeof data?.carry === "boolean") {
      item.carry = data.carry;
      if (data.carry) item.resolved = false;
    }
    if (typeof data?.resolved === "boolean") item.resolved = data.resolved;
    return json(res, 200, item);
  }
  // Team: the screen's reads and writes (in memory).
  if (path === "/api/team" && method === "GET") return json(res, 200, teamOut());
  if (path === "/api/team" && method === "PATCH") {
    if (!manager()) return json(res, 403, forbidden);
    if (typeof data?.share_new_meetings === "boolean") team.share_new_meetings = data.share_new_meetings;
    if (typeof data?.name === "string") team.name = data.name;
    if ("sprint_length" in (data ?? {}) && data.sprint_length !== null) {
      if (!Object.keys(LENGTHS).includes(data.sprint_length) && data.sprint_length !== "custom") return json(res, 422, { error: { code: "validation_error", message: "Some fields need attention.", fields: [{ field: "sprint_length", message: "Input should be a valid sprint length" }] } });
      team.sprint_length = data.sprint_length;
    }
    if ("sprint_custom_days" in (data ?? {}) && data.sprint_custom_days !== null) {
      if (!Number.isInteger(data.sprint_custom_days) || data.sprint_custom_days < 1 || data.sprint_custom_days > 365) return json(res, 422, { error: { code: "validation_error", message: "Some fields need attention.", fields: [{ field: "sprint_custom_days", message: "Input should be between 1 and 365" }] } });
      team.sprint_custom_days = data.sprint_custom_days;
    }
    if (typeof data?.sprint_align_weekdays === "boolean") {
      if (data.sprint_align_weekdays && team.sprint_length !== "week5") return json(res, 422, { error: { code: "align_needs_week5", message: "Only available for 1 week, 5 working day sprints." } });
      team.sprint_align_weekdays = data.sprint_align_weekdays;
    }
    if (typeof data?.reminders_on === "boolean") team.reminders_on = data.reminders_on;
    if (["1h", "30m", "both"].includes(data?.reminder_timing)) team.reminder_timing = data.reminder_timing;
    return json(res, 200, teamOut());
  }
  if (path === "/api/team/members" && method === "GET") return json(res, 200, membersOut());
  const oneMember = /^\/api\/team\/members\/(\d+)$/.exec(path);
  if (oneMember) {
    const member = teamMembers.find((m) => m.user_id === Number(oneMember[1]));
    if (!member) return json(res, 404, { error: { code: "not_found", message: "No such team member." } });
    if (method === "DELETE") {
      teamMembers.splice(teamMembers.indexOf(member), 1);
      res.writeHead(204);
      return res.end();
    }
    if (method === "PATCH") {
      if (data?.role && teamRole !== "owner") return json(res, 403, forbidden);
      if (data?.role) member.role = data.role;
      if (Array.isArray(data?.aliases)) member.aliases = data.aliases.map((a) => String(a).trim()).filter(Boolean);
      return json(res, 200, membersOut());
    }
  }
  if (path === "/api/team/invites" && method === "POST") {
    if (!manager()) return json(res, 403, forbidden);
    const results = (data?.invitees ?? []).map(({ email, name }) => {
      if (!/.+@.+\..+/.test(email)) return { email, status: "invalid_email", invite: null };
      if (teamMembers.some((m) => m.email === email)) return { email, status: "already_member", invite: null };
      const invite = { id: 10 + teamInvites.length, email, name: name ?? null, role: data.role, status: "pending", invited_by: "Adebayo Tosin", last_sent_at: new Date().toISOString(), expires_at: new Date(Date.now() + 7 * 864e5).toISOString() };
      teamInvites.push(invite);
      return { email, status: "sent", invite };
    });
    return json(res, 200, { results });
  }
  const oneInvite = /^\/api\/team\/invites\/(\d+)(\/resend)?$/.exec(path);
  if (oneInvite && !path.endsWith("/accept")) {
    const invite = teamInvites.find((i) => i.id === Number(oneInvite[1]));
    if (!invite) return json(res, 404, { error: { code: "not_found", message: "No such invitation." } });
    if (oneInvite[2]) return json(res, 200, { ...invite, last_sent_at: new Date().toISOString() });
    teamInvites.splice(teamInvites.indexOf(invite), 1);
    res.writeHead(204);
    return res.end();
  }
  const sharing = /^\/api\/meetings\/(\d+)\/share$/.exec(path);
  if (sharing && (method === "POST" || method === "DELETE")) {
    const found = details.find((m) => m.id === Number(sharing[1])) ?? (Number(sharing[1]) === sample.id ? sample : null);
    if (!found) return json(res, 404, { error: { code: "not_found", message: "No such meeting" } });
    found.shared_with_team = method === "POST";
    return json(res, 200, found);
  }
  // Sprints (in memory): the board's list, and Close sprint.
  if (path === "/api/sprints" && method === "GET") {
    const asked = url.searchParams.get("project");
    if (asked === null) return json(res, 200, sprintList(null));
    const project = projects.find((p) => p.id === Number(asked));
    if (!/^\d+$/.test(asked) || !project) return json(res, 404, { error: { code: "not_found", message: "No such project." } });
    return json(res, 200, sprintList(project.id));
  }
  const closing = /^\/api\/sprints\/(\d+)\/close$/.exec(path);
  if (closing && method === "POST") {
    const id = Number(closing[1]);
    const series = seriesList.find((x) => [...Array(40).keys()].some((n) => sprintId(x, x.current - 30 + n) === id));
    if (!series) return json(res, 404, { error: { code: "not_found", message: "No such sprint." } });
    if (!manager()) return json(res, 403, forbidden);
    if (id !== sprintId(series, series.current)) return json(res, 409, { error: { code: "sprint_not_current", message: "Only the current sprint can be closed." } });
    const moving = tasks.filter((t) => t.sprint_id === id && t.status !== "done");
    const number = series.current;
    const next = sprintId(series, number + 1);
    for (const t of moving) Object.assign(t, { sprint_id: next, carried: true });
    series.closed.push({ number, early: windowOf(series, number).end_date > TODAY });
    series.current += 1;
    return json(res, 200, {
      closed: sprintOut(series, number, "closed"),
      current: sprintOut(series, series.current, "current"),
      moved: moving.length,
    });
  }
  // Projects and milestones (in memory).
  if (path === "/api/projects" && method === "GET") return json(res, 200, { projects: projects.map(projectOut), can_manage: teamRole !== "member" });
  if (path === "/api/projects" && method === "POST") {
    if (teamRole === "member") return json(res, 403, { error: { code: "team_forbidden", message: "Only an owner or admin of the team can do that." } });
    const made = { id: ++nextProjectId, name: data?.name ?? "", color: data?.color ?? ["#0E6B5E", "#3C4A7A", "#8A4B00", "#8A2E5C", "#1F6FB2", "#4A524E"][projects.length % 6], keywords: data?.keywords ?? [], created_at: new Date().toISOString() };
    projects.push(made);
    return json(res, 201, projectOut(made));
  }
  const oneProject = /^\/api\/projects\/(\d+)$/.exec(path);
  if (oneProject) {
    const project = projects.find((p) => p.id === Number(oneProject[1]));
    if (!project) return json(res, 404, { error: { code: "not_found", message: "No such project." } });
    if (method === "GET") return json(res, 200, projectOut(project));
    if (teamRole === "member") return json(res, 403, { error: { code: "team_forbidden", message: "Only an owner or admin of the team can do that." } });
    if (method === "DELETE") {
      projects.splice(projects.indexOf(project), 1);
      for (const [id, pid] of Object.entries(seriesProject)) if (pid === project.id) delete seriesProject[id];
      milestones = milestones.filter((m) => m.project_id !== project.id);
      for (const t of tasks) if (t.project_id === project.id) Object.assign(t, { project_id: null, milestone_id: null, check_project: false });
      res.writeHead(204);
      return res.end();
    }
    if (method === "PATCH") {
      const { sprint_length: length, ...rest } = data ?? {};
      Object.assign(project, rest);
      if ("sprint_length" in (data ?? {})) {
        if (length !== null && !Object.keys(LENGTHS).includes(length)) return json(res, 422, { error: { code: "validation_error", message: "Some fields need attention.", fields: [{ field: "sprint_length", message: "Input should be a valid sprint length" }] } });
        // Its own pair, or back to the team's: its tasks keep their place in time.
        const before = project.sprint_length ?? null;
        const mine = tasks.filter((t) => t.project_id === project.id);
        const from = seriesOf(before === null ? null : project.id);
        const had = mine.map((t) => [t, t.sprint_id === sprintId(from, from.current) ? "current" : t.sprint_id === sprintId(from, from.current + 1) ? "next" : null]);
        project.sprint_length = length;
        if (length === null) seriesList = seriesList.filter((x) => x.pid !== project.id);
        const to = seriesOf(length === null ? null : project.id);
        for (const [t, place] of had) if (place) t.sprint_id = sprintId(to, to.current + (place === "next" ? 1 : 0));
      }
    }
    return json(res, 200, projectOut(project));
  }
  if (path === "/api/milestones" && method === "GET") return json(res, 200, { milestones: milestones.filter((m) => m.confirmed_at).map(milestoneOut) });
  const confirming = /^\/api\/milestones\/(\d+)\/confirm$/.exec(path);
  if (confirming && method === "POST") {
    const found = [...milestones, ...(proposalOpen ? [proposal] : [])].find((m) => m.id === Number(confirming[1]));
    if (!found) return json(res, 404, { error: { code: "not_found", message: "No such milestone." } });
    if (teamRole === "member") return json(res, 403, { error: { code: "team_forbidden", message: "Only an owner or admin of the team can do that." } });
    found.confirmed_at ??= new Date().toISOString();
    if (!milestones.includes(found)) milestones.push(found);
    return json(res, 200, milestoneOut(found));
  }
  const ofMeetingMs = /^\/api\/meetings\/(\d+)\/milestones$/.exec(path);
  if (ofMeetingMs && method === "GET") {
    const id = Number(ofMeetingMs[1]);
    const mine = [...milestones, ...(proposalOpen && !milestones.includes(proposal) ? [proposal] : [])].filter((m) => m.source_meeting?.id === id);
    return json(res, 200, { milestones: mine.map(milestoneOut) });
  }
  // Tasks: the list, one task, the changes and the meeting's own list (in memory).
  if (path === "/api/tasks" && method === "GET") {
    const who = url.searchParams.get("assignee");
    const sprint = url.searchParams.get("sprint");
    if (sprint !== null && !["none", "current", "next"].includes(sprint) && !/^\d+$/.test(sprint)) return json(res, 422, { error: { code: "validation_error", message: "Some fields need attention.", fields: [{ field: "sprint", message: "Use none, current, next or a sprint id" }] } });
    const inSprint = (t) => (sprint === null ? true : sprint === "none" ? t.sprint_id === null : ["current", "next"].includes(sprint) ? roleIds(sprint).includes(t.sprint_id) : t.sprint_id === Number(sprint));
    const list = tasks.filter((t) => (who === "me" ? t.assignee?.is_you : who === "none" ? !t.assignee : true) && inSprint(t));
    return json(res, 200, { tasks: list, people, attention: attention(), has_more: false, next_before_id: null });
  }
  if (path === "/api/tasks" && method === "POST") {
    const pid = data?.project_id ?? null;
    if (data && "sprint_id" in data && data.sprint_id !== null && !openIdsFor(pid).includes(data.sprint_id)) return json(res, 422, notOnTeam);
    const made = makeTask(++nextTaskId, data?.title ?? "", data?.assignee_user_id ?? null, data?.due_date ?? null, data?.status ?? "todo", null, { created_at: new Date().toISOString(), project_id: pid });
    // Left out: filed by the due date; null is the Backlog (the FE guide's field-nesting trap).
    made.sprint_id = data && "sprint_id" in data ? data.sprint_id : fileByDue(made.due_date, pid);
    made.carried = false;
    tasks.unshift(made);
    return json(res, 201, made);
  }
  const oneTask = /^\/api\/tasks\/(\d+)$/.exec(path);
  if (oneTask) {
    const task = tasks.find((t) => t.id === Number(oneTask[1]));
    if (!task) return json(res, 404, { error: { code: "not_found", message: "No such task" } });
    if (method === "DELETE") {
      tasks.splice(tasks.indexOf(task), 1);
      res.writeHead(204);
      return res.end();
    }
    if (method === "PATCH") {
      for (const [key, value] of Object.entries(data ?? {})) {
        if (key === "assignee_user_id") task.assignee = personOf(value);
        else if (key === "project_id") {
          rehome(task, task.project_id, value);
          Object.assign(task, { project_id: value, check_project: false });
        } else if (key === "sprint_id") {
          if (value !== null && !openIdsFor(task.project_id).includes(value)) return json(res, 422, notOnTeam);
          Object.assign(task, { sprint_id: value, carried: false });
        } else task[key] = value;
      }
      if (data?.status) task.is_new = false;
    }
    return json(res, 200, task);
  }
  const ofMeeting = /^\/api\/meetings\/(\d+)\/tasks$/.exec(path);
  if (ofMeeting && method === "GET") {
    const list = meetingTasks(Number(ofMeeting[1]));
    return json(res, 200, { tasks: list, assigned_people: new Set(list.filter((t) => t.assignee).map((t) => t.assignee.user_id)).size, extraction_status: "done" });
  }
  if (path === "/api/integrations/linear/teams") return json(res, 200, integrationsCapture("23-linear-teams"));
  if (path === "/api/integrations/linear") {
    if (method === "PUT") {
      linearConnected = true;
      return json(res, 200, integrationsCapture("25-linear-connect"));
    }
    if (method === "DELETE") {
      linearConnected = false;
      res.writeHead(204);
      return res.end();
    }
    return json(res, 200, integrationsCapture(linearConnected ? "26-linear-connected" : "19-linear-not-connected"));
  }
  if (path === "/api/integrations/slack") {
    if (method === "PUT") {
      slackConnected = true;
      return json(res, 200, integrationsCapture("10-slack-connected"));
    }
    if (method === "DELETE") {
      slackConnected = false;
      res.writeHead(204);
      return res.end();
    }
    return json(res, 200, integrationsCapture(slackConnected ? "10-slack-connected" : "05-slack-not-connected"));
  }
  if (path === "/api/settings/tasks") {
    if (method === "PUT") tasksSettings = { auto_create_tasks: !!data?.auto_create_tasks, email_assignees: !!data?.email_assignees };
    return json(res, 200, tasksSettings);
  }
  // Share notes by email: the refusals replay the real captures in docs/fe-captures/share/; a good
  // request is kept in memory and answered like the real one (a recipient is `sent` at once).
  if (path === "/api/mail/opt-out" && method === "POST") {
    if (data?.token === "dead") return json(res, 400, shareCapture("16-optout-garbage"));
    return json(res, 200, shareCapture("14-optout-ok"));
  }
  const shareRoute = /^\/api\/meetings\/(\d+)\/shares$/.exec(path);
  if (shareRoute) {
    if (![sample.id, withStranger.id].includes(Number(shareRoute[1]))) return json(res, 404, { error: { code: "not_found", message: "No such meeting" } });
    if (method === "GET") return json(res, 200, { shares });
    const people = data?.recipients ?? [];
    if (!["en", "fr", "es"].includes(data?.language)) return json(res, 422, shareCapture("20-share-language-unsupported"));
    if (people.length > 10) return json(res, 422, shareCapture("21-share-too-many"));
    if (data?.message === "limit") return json(res, 429, shareCapture("37-share-limit"));
    shareId += 1;
    const waiting = data.language !== "en" && !translationOf(data.language);
    shares.unshift({
      share_id: shareId,
      status: waiting ? "waiting_translation" : "queued",
      language: data.language,
      attach: data.attach ?? "none",
      include_transcript: !!data.include_transcript,
      message: data.message ?? "",
      created_at: new Date().toISOString(),
      recipients: people.map((r) => ({
        email: r.email,
        name: r.name ?? teamMembers.find((m) => m.email === r.email)?.name ?? null,
        status: waiting ? "pending" : "sent",
        is_team_member: teamMembers.some((m) => m.email === r.email),
      })),
    });
    res.writeHead(202, { "content-type": "application/json" });
    return res.end(JSON.stringify({ share_id: shareId, status: shares[0].status }));
  }
  const translate = /^\/api\/meetings\/(\d+)\/translations(?:\/(\w+))?$/.exec(path);
  if (translate) {
    const language = method === "POST" ? data?.language : translate[2];
    if (Number(translate[1]) !== sample.id) return json(res, 404, { error: { code: "not_found", message: "No such meeting" } });
    if (!["fr", "es"].includes(language)) return method === "POST" ? json(res, 422, translateCapture("23-translate-language-unsupported")) : json(res, 404, noTranslation);
    if (method === "POST") {
      const known = translationOf(language);
      if (known?.status === "ready") return json(res, 200, known);
      translating[language] ??= Date.now();
      return json(res, 202, translationOf(language));
    }
    if (method === "DELETE") {
      delete translating[language];
      res.writeHead(204);
      return res.end();
    }
    const found = translationOf(language);
    return found ? json(res, 200, found) : json(res, 404, noTranslation);
  }
  const detail = /^\/api\/meetings\/(\d+)$/.exec(path);
  if (detail && method === "DELETE") {
    // 204 for a meeting that can go; the replay of the real 409 for one still being worked on.
    const at = details.findIndex((m) => m.id === Number(detail[1]));
    if (at === -1 && Number(detail[1]) !== sample.id) return json(res, 404, { error: { code: "not_found", message: "No such meeting" } });
    if (at !== -1 && !details[at].can_delete) return json(res, 409, { error: { code: "meeting_active", message: "This meeting is still being recorded or its notes are being written. Delete it once it has finished." } });
    if (at !== -1) details.splice(at, 1);
    res.writeHead(204);
    return res.end();
  }
  if (detail && method === "GET") {
    const meeting =
      Number(detail[1]) === sample.id
        ? { ...sample, translations: ["fr", "es"].filter((l) => translationOf(l)).map((language) => ({ language, status: translationOf(language).status, stale: false })) }
        : Number(detail[1]) === withStranger.id
          ? withStranger
          : details.find((m) => m.id === Number(detail[1]));
    if (meeting && meeting !== sample && meeting !== withStranger) Object.assign(meeting, { tasks: meetingTasks(meeting.id), tasks_count: meetingTasks(meeting.id).length });
    return meeting ? json(res, 200, meeting) : json(res, 404, { error: { code: "not_found", message: "That meeting was not found." } });
  }
  // Email preferences and the bell: the captures in docs/fe-captures/prelaunch-mail-prefs/. The
  // switch keeps its state in memory so the screen can be flipped by hand.
  if (path === "/api/settings/language") {
    if (method === "PUT") {
      if (!["en", "fr", "es"].includes(data?.default_language)) return json(res, 422, translateCapture("20-language-set-unsupported"));
      defaultLanguage = data.default_language;
    }
    return json(res, 200, { default_language: defaultLanguage });
  }
  if (path === "/api/settings/time-zone") {
    if (method === "PUT") {
      try {
        new Intl.DateTimeFormat("en", { timeZone: data?.time_zone });
      } catch {
        return json(res, 422, { error: { code: "time_zone_unsupported", message: "That time zone is not one we know. Pick one from the list." } });
      }
      timeZone = data.time_zone;
    }
    return json(res, 200, { time_zone: timeZone ?? "UTC", is_set: timeZone !== null });
  }
  if (path === "/api/ask/scope" && method === "GET") {
    if (values.ask === "scope-failed") return json(res, 500, { error: { code: "server_error", message: "Something went wrong on our side." } });
    const none = values.ask === "empty";
    return json(res, 200, {
      meetings: none ? 0 : 12,
      decisions: none ? 0 : 31,
      tasks: none ? 0 : 44,
      suggestions: ["What did we decide about pricing?", "What did I commit to last week?", "What did Daniel say?", "When is the launch?", "Which decisions were made on Monday?"],
    });
  }
  if (path === "/api/ask" && method === "POST") {
    const question = String(data?.question ?? "").trim();
    if (!question || question.length > 300) return json(res, 422, { error: { code: "question_invalid", message: "Type a question of up to 300 characters." } });
    await wait(900);
    const q = question.toLowerCase();
    const cite = (meeting_id, title, date_label, target, line, owner_name = null) => ({ meeting_id, title, date_label, owner_name, shared: owner_name !== null, target, line });
    if (q.includes("fail") && armed.ask) {
      armed.ask = false;
      return json(res, 502, { error: { code: "ask_failed", message: "Beyric could not answer that. Try again." } });
    }
    if (q.includes("limit")) return json(res, 429, { error: { code: "ask_limit", message: "You have asked 30 questions today. Try again tomorrow." } });
    if (q.includes("rate")) return json(res, 429, { error: { code: "ask_rate", message: "Slow down a little and try again in a minute." } });
    if (q.includes("zebra") || values.ask === "empty") return json(res, 200, { kind: "none", lead: "I could not find that in your meetings.", points: [] });
    if (q.includes("tasks") || q.includes("commit")) {
      return json(res, 200, {
        kind: "tasks",
        lead: "You made 3 commitments in Beyric Monday planning on Mon 2 Nov. None of them are done yet.",
        points: [
          { text: "Finish payment checkout. Due Fri 6 Nov.", cites: [cite(1, "Beyric Monday planning", "Mon 2 Nov", "transcript", 1)] },
          { text: "Support Daniel Okoro with testing during the week. Due Fri 6 Nov.", cites: [cite(1, "Beyric Monday planning", "Mon 2 Nov", "notes", null)] },
          { text: "Share the meeting summary with the wider team. No due date.", cites: [cite(1, "Beyric Monday planning", "Mon 2 Nov", "notes", null)] },
        ],
      });
    }
    if (q.includes("gone")) {
      return json(res, 200, { kind: "answer", lead: "This was covered in a meeting that is no longer there.", points: [{ text: "The launch date was set to December 8.", cites: [cite(999, "Launch review", "Fri 30 Oct", "transcript", 3)] }] });
    }
    if (q.includes("long")) {
      return json(res, 200, { kind: "answer", lead: "Here is what the long titles look like on a narrow screen.", points: [{ text: "A source whose title and owner make a very long label.", cites: [cite(8, "Weekly product, design and engineering planning with the customer success team", "Mon 2 Nov", "transcript", 2, "Ojebola Grace"), cite(1, "Beyric Monday planning", "Mon 2 Nov", "notes", null)] }] });
    }
    if (q.includes("pric")) {
      return json(res, 200, {
        kind: "answer",
        lead: "No pricing model has been chosen yet. It is planned for the next Monday planning, and it has come up in 3 meetings.",
        points: [
          { text: "Adebayo listed the options for a note taker built for internal use.", cites: [cite(8, "Zoom 88205679674", "Mon 2 Nov", "transcript", 10)] },
          { text: "Customer interviews will happen once onboarding is stable.", cites: [cite(8, "Zoom 88205679674", "Mon 2 Nov", "transcript", 12), cite(9, "Zoom 99205679674", "Mon 2 Nov", "transcript", 15, "Daniel")] },
          { text: "It is logged as a proposed decision.", cites: [cite(1, "Beyric Monday planning", "Mon 2 Nov", "notes", null)] },
        ],
      });
    }
    return json(res, 200, { kind: "answer", lead: "Here is what your meetings say about that.", points: [{ text: "It came up in the Monday planning.", cites: [cite(1, "Beyric Monday planning", "Mon 2 Nov", "notes", null)] }] });
  }
  if (path === "/api/decisions" && method === "GET") {
    // `retry-loading`: the first answer fails (the page draws "Could not load"), then each one is slow, so
    // pressing Try again shows the loading line.
    if (values.decisions === "retry-loading" && armed.decisions) {
      armed.decisions = false;
      return json(res, 500, { error: { code: "server_error", message: "Something went wrong on our side." } });
    }
    if (values.decisions === "failed") return json(res, 500, { error: { code: "server_error", message: "Something went wrong on our side." } });
    if (values.decisions === "loading" || values.decisions === "retry-loading") await wait(4000);
    if (values.decisions === "empty") return json(res, 200, { groups: [], has_more: false, next_before_id: null });
    // The search "zebra" answers the captured no-match list (docs/fe-captures/ai-workspace-w1/16-...).
    if (url.searchParams.get("q") === "zebra") return json(res, 200, wsCapture("16-decisions-search-none"));
    return json(res, 200, wsCapture("13-decisions"));
  }
  if (/^\/api\/decisions\/\d+\/hide$/.test(path) && method === "POST") {
    res.writeHead(204);
    return res.end();
  }
  if (path === "/api/review" && method === "GET") {
    // `retry-loading`: the first answer fails (the page draws "Could not load"), then each one is slow, so
    // pressing Try again shows the loading line.
    if (values.review === "retry-loading" && armed.review) {
      armed.review = false;
      return json(res, 500, { error: { code: "server_error", message: "Something went wrong on our side." } });
    }
    if (values.review === "failed") return json(res, 500, { error: { code: "server_error", message: "Something went wrong on our side." } });
    if (values.review === "loading" || values.review === "retry-loading") await wait(4000);
    const checked = url.searchParams.get("state") === "checked";
    if (values.review === "empty") return json(res, 200, { items: [], open_count: 0, truncated: false });
    return json(res, 200, wsCapture(checked ? "33-review-checked" : "29-review-open"));
  }
  if (/^\/api\/review\/\d+\/vocabulary$/.test(path) && method === "POST") {
    if (!String(data?.term ?? "").trim()) return json(res, 400, wsCapture("35-review-vocabulary-invalid"));
    if (["Dup", "Beyric"].includes(data.term)) return json(res, 409, wsCapture("38-review-vocabulary-duplicate"));
    if (data.term === "Gone") return json(res, 404, wsCapture("40-review-check-foreign"));
    return json(res, 201, wsCapture("36-review-vocabulary"));
  }
  if (/^\/api\/review\/\d+\/check$/.test(path) && method === "POST") return json(res, 200, wsCapture("31-review-check"));
  if (/^\/api\/review\/\d+\/uncheck$/.test(path) && method === "POST") return json(res, 200, wsCapture("34-review-uncheck"));
  if (path === "/api/settings/email") {
    if (method === "PUT") notifyEmail = data?.notify_email === true;
    return json(res, 200, { notify_email: notifyEmail });
  }
  if (path === "/api/notifications" && method === "GET") return json(res, 200, prelaunchBody("19-notifications-cap-notice"));
  if (path === "/api/notifications/read-all") return json(res, 200, prelaunchBody("21-notifications-read-all"));
  if (path === "/api/templates" && method === "GET") return json(res, 200, { templates });
  const removal = /^\/api\/templates\/(\d+)$/.exec(path);
  if (removal && method === "DELETE") {
    const at = templates.findIndex((t) => t.id === Number(removal[1]));
    if (at === -1) return json(res, 404, deleteCapture("12-delete-again"));
    if (templates.length === 1) return json(res, 409, deleteCapture("25-delete-last"));
    templates.splice(at, 1);
    res.writeHead(204, { "cache-control": "no-store" });
    return res.end();
  }
  if (path === "/api/calendar" && method === "GET") {
    if (values.calendar === "off") return json(res, 404, calendarCapture("04-state-feature-off"));
    if (values.calendar === "paused") {
      // Connected with events, and the operator's emergency stop on (the 22 capture's line).
      return json(res, 200, {
        ...calendarCapture("09-state-connected-with-upcoming"),
        auto_join_paused: true,
        auto_join_paused_text: calendarCapture("22-state-auto-join-paused").auto_join_paused_text,
      });
    }
    if (values.calendar === "needs_reconnect") return json(res, 200, calendarCapture("15-state-needs-reconnect-revoked"));
    return json(res, 200, calendarCapture(values.calendar === "connected" ? "09-state-connected-with-upcoming" : "05-state-not-connected"));
  }
  if (path === "/api/calendar/recurring" && method === "GET") {
    if (values.calendar === "off") return json(res, 404, recurringCapture("04-feature-off"));
    if (values.calendar !== "connected" || values.recurring === "none") return json(res, 200, recurringCapture("05-not-connected"));
    const zone = url.searchParams.get("tz");
    if (zone === "Mars/Olympus") return json(res, 422, recurringCapture("12-unknown-timezone"));
    const file = zone === "Africa/Lagos" ? "10-series-lagos" : zone === "Pacific/Auckland" ? "11-series-auckland" : "09-series-utc";
    const answer = recurringCapture(file);
    // `--recurring two`: only the two weekly "Beyric" series, the two cards the designer drew.
    if (values.recurring === "two") answer.series = answer.series.filter((card) => card.name.startsWith("Beyric"));
    return json(res, 200, answer);
  }
  // The write routes answer with bodies captured from the running app (docs/fe-captures/
  // calendar-settings/): the rule's state as it was captured, the template's, and one row for a
  // switch with the words the server gave it. Nothing here decides anything; it only replays.
  if (path === "/api/calendar/settings" && method === "PUT") {
    const data = await readJson(req);
    const file = data?.rule
      ? { organizer: "32-settings-rule-organizer", none: "18-settings-rule-none", all: "24-settings-rule-all" }[data.rule]
      : data?.calendar_template_id === null
        ? "10-settings-template-null"
        : "08-settings-template";
    return json(res, 200, settingsCapture(file));
  }
  const override = /^\/api\/calendar\/events\/(\d+)\/override$/.exec(path);
  if (override && method === "PUT") {
    const data = await readJson(req);
    const row = calendarCapture("09-state-connected-with-upcoming").upcoming.find((e) => e.id === Number(override[1]));
    if (!row) return json(res, 404, { error: { code: "not_found", message: "No such event" } });
    return json(
      res,
      200,
      data?.override
        ? { ...row, will_join: true, will_join_text: "Will join", reason_text: null }
        : { ...row, will_join: false, will_join_text: "Off", reason_text: "You switched this meeting off" },
    );
  }
  if (path === "/api/calendar/google/connect" && method === "POST") {
    return json(res, 200, { authorize_url: "https://accounts.example.test/consent", show_unverified_notice: false });
  }
  if (path === "/api/calendar/google/disconnect" && method === "POST") return json(res, 200, { status: "disconnected", revoked: true });
  if (path === "/api/vocabulary" && method === "GET") return json(res, 200, { terms });
  if (path.startsWith("/api/")) return json(res, 200, {});
  res.writeHead(404, { "content-type": "text/plain" });
  res.end("not found (mock api)");
});

server.listen(Number(values.port), "127.0.0.1", () => {
  console.log(`mock api on http://127.0.0.1:${values.port}`);
});
