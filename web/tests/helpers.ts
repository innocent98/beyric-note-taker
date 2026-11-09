import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

export const WEB = join(__dirname, "..");
export const REPO = join(WEB, "..");

export function read(path: string): string {
  return readFileSync(path, "utf8");
}

// All files under `dir` (relative to web/) with one of the extensions, skipping build output.
export function filesUnder(dir: string, exts: string[]): string[] {
  const out: string[] = [];
  const walk = (current: string) => {
    for (const name of readdirSync(current)) {
      if (name === "node_modules" || name === ".next") continue;
      const full = join(current, name);
      if (statSync(full).isDirectory()) walk(full);
      else if (exts.some((e) => name.endsWith(e))) out.push(full);
    }
  };
  walk(join(WEB, dir));
  return out;
}

export function rel(path: string): string {
  return relative(WEB, path).split(sep).join("/");
}

// Meeting captures made before the meeting page was drawn lack `duration_seconds`, `participants`,
// `decisions_count` and `review_open_count`, which the API now always sends (nothing is invented:
// the values are what a meeting with no recording, no speakers and nothing extracted has).
function withDetailDefaults<T>(body: T): T {
  // Decision lists captured before the Decisions record (migration 0036) lack the status, the evidence,
  // the links, `can_edit` and the page's `counts` and `due_this_week`, which the API now always sends:
  // what an old decision reads as is Approved with nothing else, and the owner may change it.
  if (body && typeof body === "object" && "groups" in body && "has_more" in body && !("counts" in body)) {
    const groups = (body as { groups: Array<{ decisions: Array<Record<string, unknown>> }> }).groups;
    for (const group of groups) {
      group.decisions = group.decisions.map((d) => ({
        status: "approved", decided_by: null, quote: null, quote_speaker: null, line: null, revisit_on: null,
        replaced_by: null, replaces: [], related_tasks: [], can_edit: true, ...d,
      }));
    }
    const all = groups.reduce((n, g) => n + g.decisions.length, 0);
    Object.assign(body, { counts: { all, approved: all, proposed: 0, replaced: 0, rejected: 0 }, due_this_week: 0 });
  }
  if (body && typeof body === "object" && "notes_html" in body && !("participants" in body)) {
    Object.assign(body, { duration_seconds: null, participants: [], decisions_count: 0, review_open_count: 0 });
  }
  if (body && typeof body === "object" && "notes_html" in body && !("can_delete" in body)) {
    // Captures made before `can_delete`: the API's rule is the owner of a meeting that is not active.
    Object.assign(body, { can_delete: !("status" in body && ["bot_sent", "processing"].includes(String(body.status))) });
  }
  // Captures made before W3 lack the share fields: a meeting its owner has not shared.
  if (body && typeof body === "object" && "notes_html" in body && !("shared_with_team" in body)) {
    Object.assign(body, { shared_with_team: false, can_share: true, shared_by: null });
  }
  // Captures made before Tasks lack `tasks` and `tasks_count`, which the API now always sends.
  if (body && typeof body === "object" && "notes_html" in body && !("tasks" in body)) {
    Object.assign(body, { tasks: [], tasks_count: 0 });
  }
  if (body && typeof body === "object" && Array.isArray((body as { meetings?: unknown }).meetings)) {
    for (const row of (body as unknown as { meetings: Record<string, unknown>[] }).meetings) row.tasks_count ??= 0;
  }
  return body;
}

// A response body captured from the running API (docs/fe-captures/owner-app/<file>).
export function captured<T>(file: string): T {
  const body = JSON.parse(read(join(REPO, "docs/fe-captures/owner-app", file))).response.body;
  // Meeting captures made before W7 lack `translations`, which the API now always sends.
  if (body && typeof body === "object" && "notes_html" in body && !("translations" in body)) body.translations = [];
  return withDetailDefaults(body) as T;
}

// A capture from the running API for the translation routes and the default language
// (docs/fe-captures/translate/<file>, `python scripts/capture_translate_api.py`).
type TranslateCapture = {
  request: { body: unknown };
  response: { status: number; body: { error: { code: string; message: string } } & Record<string, unknown> };
};
export function translateCapture(file: string): TranslateCapture {
  return JSON.parse(read(join(REPO, "docs/fe-captures/translate", file)));
}
export function translateBody<T>(file: string): T {
  return withDetailDefaults(translateCapture(file).response.body) as T;
}

// A capture of the template delete (docs/fe-captures/template-delete/<file>): the whole exchange.
export function capturedDelete(file: string): { request: { method: string; path: string }; response: { status: number; headers: Record<string, string>; body: unknown } } {
  return JSON.parse(read(join(REPO, "docs/fe-captures/template-delete", file)));
}

type AccountCapture = { request: { body: unknown }; response: { status: number; body: unknown } };

// A capture from the running API for the Decisions record (docs/fe-captures/decisions/<file>, `python
// scripts/capture_decisions_api.py`): the response body, or the whole exchange.
export function decisionsCapture(file: string): { request: { method: string; path: string; body: unknown }; response: { status: number; body: unknown } } {
  return JSON.parse(read(join(REPO, "docs/fe-captures/decisions", file)));
}
export function decisionsBody<T>(file: string): T {
  return decisionsCapture(file).response.body as T;
}

// A capture from the running API for the accounts routes (docs/fe-captures/pilot-accounts/<file>).
export function capturedAccount(file: string): AccountCapture {
  return JSON.parse(read(join(REPO, "docs/fe-captures/pilot-accounts", file)));
}

// The body of an account capture.
export function accountBody<T>(file: string): T {
  return capturedAccount(file).response.body as T;
}

// The body of a capture from the running API for the AI workspace routes
// (docs/fe-captures/ai-workspace-w1/<file>).
export function workspaceBody<T>(file: string): T {
  return withDetailDefaults(JSON.parse(read(join(REPO, "docs/fe-captures/ai-workspace-w1", file))).response.body) as T;
}

// A capture from the running API for email preferences and the two notices
// (docs/fe-captures/prelaunch-mail-prefs/<file>).
export function prelaunchCapture(file: string): { request: { body: unknown }; response: { status: number; body: unknown } } {
  return JSON.parse(read(join(REPO, "docs/fe-captures/prelaunch-mail-prefs", file)));
}

// The error a captured failing response carries (the `ApiError` arguments).
export function workspaceError(file: string): { status: number; code: string; message: string } {
  const { response } = JSON.parse(read(join(REPO, "docs/fe-captures/ai-workspace-w1", file)));
  return { status: response.status, ...response.body.error };
}

// A capture from the running API for the calendar state route (docs/fe-captures/calendar-state/<file>).
export function calendarBody<T>(file: string): T {
  return JSON.parse(read(join(REPO, "docs/fe-captures/calendar-state", file))).response.body as T;
}

// A capture from the "add the note taker to your event" tip (docs/fe-captures/bot-invite-tip/<file>):
// the calendar state or a meeting, with `bot_invite_email` set or `null`.
export function inviteTipBody<T>(file: string): T {
  return JSON.parse(read(join(REPO, "docs/fe-captures/bot-invite-tip", file))).response.body as T;
}

// A capture from Send now and Not recorded (docs/fe-captures/send-now/<file>): the answer, and the
// `ApiError` arguments of a failing one.
export function sendNowBody<T>(file: string): T {
  return JSON.parse(read(join(REPO, "docs/fe-captures/send-now", file))).response.body as T;
}
export function sendNowError(file: string): { status: number; code: string; message: string } {
  const { response } = JSON.parse(read(join(REPO, "docs/fe-captures/send-now", file)));
  return { status: response.status, ...response.body.error };
}

// A capture from the calendar connect routes (docs/fe-captures/calendar-connect/<file>).
export function connectBody<T>(file: string): T {
  return JSON.parse(read(join(REPO, "docs/fe-captures/calendar-connect", file))).response.body as T;
}

// A capture from the calendar write routes (docs/fe-captures/calendar-settings/<file>): the body of
// the response, or the error it carried.
export function settingsBody<T>(file: string): T {
  return JSON.parse(read(join(REPO, "docs/fe-captures/calendar-settings", file))).response.body as T;
}

export function settingsError(file: string): { status: number; code: string; message: string } {
  const { response } = JSON.parse(read(join(REPO, "docs/fe-captures/calendar-settings", file)));
  return { status: response.status, ...response.body.error };
}

// The body of a capture from the running API for the Tasks routes
// (docs/fe-captures/tasks-w2/<file>, `python scripts/capture_tasks_api.py`).
export function tasksBody<T>(file: string): T {
  const body = JSON.parse(read(join(REPO, "docs/fe-captures/tasks-w2", file))).response.body;
  // Captures made before W4 lack the project fields, which the API now always sends (a task filed
  // under no project; nothing to check).
  const fill = (task: Record<string, unknown>) => {
    task.project_id ??= null;
    task.milestone_id ??= null;
    task.check_project ??= false;
    // Captures made before Suggested tasks lack these, which the API now always sends.
    task.suggested ??= false;
    task.confidence ??= null;
    task.reason ??= null;
    task.quote_speaker ??= null;
    task.quote_line ??= null;
    task.can_decide ??= false;
  };
  if (body && Array.isArray(body.tasks)) {
    body.tasks.forEach(fill);
    if (body.attention) body.attention.check_project ??= 0;
  } else if (body && typeof body === "object" && "can_update" in body) fill(body);
  return body as T;
}

// The body of a capture from the running API for Suggested tasks
// (docs/fe-captures/suggested-tasks/<file>, `python scripts/capture_suggested_tasks_api.py`; the
// model's reply in those captures is a labelled stand-in).
export function suggestedBody<T>(file: string): T {
  return JSON.parse(read(join(REPO, "docs/fe-captures/suggested-tasks", file))).response.body as T;
}

// The body of a capture from the running API for the Team routes
// (docs/fe-captures/team-w3/<file>, `python scripts/capture_team_api.py`).
export function teamBody<T>(file: string): T {
  return JSON.parse(read(join(REPO, "docs/fe-captures/team-w3", file))).response.body as T;
}

// A capture from the Slack integration routes (docs/fe-captures/integrations-w10/<file>): the body of
// the response, or the error a failing one carries.
export function integrationsBody<T>(file: string): T {
  return JSON.parse(read(join(REPO, "docs/fe-captures/integrations-w10", file))).response.body as T;
}

export function integrationsError(file: string): { status: number; code: string; message: string } {
  const { response } = JSON.parse(read(join(REPO, "docs/fe-captures/integrations-w10", file)));
  return { status: response.status, ...response.body.error };
}

// The body of a capture from the running API for projects and milestones
// (docs/fe-captures/projects-w4/<file>, `python scripts/capture_projects_api.py`).
export function projectsBody<T>(file: string): T {
  return JSON.parse(read(join(REPO, "docs/fe-captures/projects-w4", file))).response.body as T;
}

// The body of a capture from the running API for linking a recurring meeting to a project and the
// Series page's sprint line (docs/fe-captures/series-project-link/<file>,
// `python scripts/capture_series_project_api.py`).
export function linkBody<T>(file: string): T {
  return JSON.parse(read(join(REPO, "docs/fe-captures/series-project-link", file))).response.body as T;
}

// The body of a capture from the running API for the sprint routes
// (docs/fe-captures/sprints-w9/<file>, `python scripts/capture_sprints_api.py`).
export function sprintsBody<T>(file: string): T {
  return JSON.parse(read(join(REPO, "docs/fe-captures/sprints-w9", file))).response.body as T;
}

// A capture from the running API for the Share routes (docs/fe-captures/share/<file>,
// `python scripts/capture_share_api.py`).
export function shareCapture(file: string): { request: { body: unknown }; response: { status: number; body: unknown } } {
  return JSON.parse(read(join(REPO, "docs/fe-captures/share", file)));
}
export function shareBody<T>(file: string): T {
  return shareCapture(file).response.body as T;
}
