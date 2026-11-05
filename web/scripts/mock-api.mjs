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
import { readFileSync } from "node:fs";
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
    "time-zone": { type: "string" },
  },
});

// Decisions and Review: `--decisions live|loading|failed|empty|retry-loading` and `--review ...` (same words)
// pick what those lists answer (the captures in docs/fe-captures/ai-workspace-w1/; `loading` answers
// after 4 s, `failed` is a 500, `empty` has no item). Removing, checking and adding a term answer the
// captures too; the terms "Dup" and "Beyric" answer the captured 409 and "Gone" a 404 (the item's meeting deleted).
// `--time-zone Africa/Lagos` starts the account with that zone chosen (without it GET
// /api/settings/time-zone answers UTC, not set); PUT keeps what it is given, in memory.

// Translation (W7): `--language fr|es|en` is the account's default notes language to start with.
// Meeting 8 is the designer's sample meeting ("Zoom 88205679674", the notes and transcript of
// docs/fe-captures/translate/); it is not in the list. Asking for French or Spanish on it answers
// the captured 202, then the captured translation 1.5 seconds later. Nothing is translated here:
// the French and Spanish are the designer's own words, as captured with the model faked.

// `--calendar not_connected|connected|off`: what GET /api/calendar answers (the captures in
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
  if (ready) meeting.extraction_status = "done";
}
// The owner-app captures predate `template_name`; the running API sends it on every meeting.
for (const meeting of details) meeting.template_name = body("11-templates-seeded").templates.find((t) => t.id === meeting.template_id)?.name ?? "General";
const summary = ({ id, title, platform, native_id, status, step, status_text, status_tone, template_id, template_name, created_at }) => ({
  id, title, platform, native_id, status, step, status_text, status_tone, template_id, template_name, created_at,
  tasks_count: meetingTasks(id).length,
});

// Tasks (W2). The shape is the one in docs/fe-captures/tasks-w2/ (`can_*`, `assignee`, `meeting`...);
// the values are the mock's own: the designer's sample tasks (Beyric Monday planning, 2 Nov), so a
// screen can be set next to the prototype. Writes change them in memory. Meeting 1 carries the first
// eight, as in the design; the others point at meetings that are not in the list.
const me = { user_id: 2, name: "Adebayo Tosin", is_you: true };
const people = [me, { user_id: 3, name: "Daniel Okoro", is_you: false }, { user_id: 4, name: "Amara Nwosu", is_you: false }];
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
  created_at: "2026-11-02T10:00:00Z", from_meeting: !!ref, meeting: ref, ...allowed, ...extra,
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
const meetingTasks = (id) => tasks.filter((t) => t.meeting?.id === id).sort((a, b) => b.id - a.id);
const tasksNew = () => tasks.filter((t) => t.is_new && t.status !== "done").length;
const attention = () => ({ needs_owner: tasks.filter((t) => !t.assignee && t.status !== "done").length, unverified: 0 });
let tasksSettings = { auto_create_tasks: true, email_assignees: true };
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
};
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
const mockUser = () => ({ email: "owner@example.test", name: null, is_admin: false, email_verified: true, time_zone: timeZone });
const wsCapture = (name) => JSON.parse(readFileSync(join(captureDir, "ai-workspace-w1", `${name}.json`), "utf8")).response.body;
const armed = { decisions: true, review: true }; // `retry-loading` fails once after each reset
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
    for (const key of ["decisions", "review"]) if (url.searchParams.has(key)) values[key] = url.searchParams.get(key);
    return json(res, 200, { decisions: values.decisions, review: values.review });
  }
  // Not part of the API: lets parity-shots.mjs start each translation screen from nothing.
  if (path === "/__mock/reset") {
    for (const language of Object.keys(translating)) delete translating[language];
    defaultLanguage = values.language;
    armed.decisions = armed.review = true;
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
  if (path.startsWith("/api/") && !loggedIn) {
    return json(res, 401, { error: { code: "not_authenticated", message: "Log in to continue." } });
  }
  if (path === "/api/meetings" && method === "GET") {
    const meetings = values.empty ? [] : details.map(summary).reverse();
    return json(res, 200, { meetings, has_more: false, next_before_id: null, worker_stale: values.stale || values.empty });
  }
  // Tasks: the list, one task, the changes and the meeting's own list (in memory).
  if (path === "/api/tasks" && method === "GET") {
    const who = url.searchParams.get("assignee");
    const list = tasks.filter((t) => (who === "me" ? t.assignee?.is_you : who === "none" ? !t.assignee : true));
    return json(res, 200, { tasks: list, people, attention: attention(), has_more: false, next_before_id: null });
  }
  if (path === "/api/tasks" && method === "POST") {
    const made = makeTask(++nextTaskId, data?.title ?? "", data?.assignee_user_id ?? null, data?.due_date ?? null, data?.status ?? "todo", null, { created_at: new Date().toISOString() });
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
        else task[key] = value;
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
  if (path === "/api/settings/tasks") {
    if (method === "PUT") tasksSettings = { auto_create_tasks: !!data?.auto_create_tasks, email_assignees: !!data?.email_assignees };
    return json(res, 200, tasksSettings);
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
  if (detail && method === "GET") {
    const meeting =
      Number(detail[1]) === sample.id
        ? { ...sample, translations: ["fr", "es"].filter((l) => translationOf(l)).map((language) => ({ language, status: translationOf(language).status, stale: false })) }
        : details.find((m) => m.id === Number(detail[1]));
    if (meeting && meeting !== sample) Object.assign(meeting, { tasks: meetingTasks(meeting.id), tasks_count: meetingTasks(meeting.id).length });
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
