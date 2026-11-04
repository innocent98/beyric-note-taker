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

// A response body captured from the running API (docs/fe-captures/owner-app/<file>).
export function captured<T>(file: string): T {
  return JSON.parse(read(join(REPO, "docs/fe-captures/owner-app", file))).response.body as T;
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
  return JSON.parse(read(join(REPO, "docs/fe-captures/ai-workspace-w1", file))).response.body as T;
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
