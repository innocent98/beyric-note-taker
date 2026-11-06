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
  return JSON.parse(read(join(REPO, "docs/fe-captures/tasks-w2", file))).response.body as T;
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
