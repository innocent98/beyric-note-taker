import type {
  AgendaItem,
  AskAnswer,
  AskScope,
  AgendaSent,
  CalendarEvent,
  CalendarProvider,
  CalendarSettingsInput,
  CalendarState,
  ConnectStart,
  Decision,
  DecisionChanges,
  DecisionList,
  DecisionStatus,
  EmailPreferences,
  DisconnectResult,
  NotRecordedList,
  RecurringState,
  SendNowResult,
  ExportFormat,
  FieldError,
  LanguagePreferences,
  TimeZonePreferences,
  RecordingAck,
  Meeting,
  MeetingList,
  MeetingMilestones,
  MeetingTasks,
  Milestone,
  MilestoneList,
  NewMeeting,
  NewTask,
  NoteLanguage,
  NoticeList,
  MeetingSeriesResult,
  OpenQuestion,
  ReminderPreferences,
  SeriesCard,
  QuestionChanges,
  SeriesList,
  SeriesPage,
  Pilot,
  Project,
  ProjectChanges,
  ProjectList,
  ReviewItem,
  ReviewList,
  ReviewState,
  ReviewTermResult,
  SessionState,
  SignUpBody,
  SprintClosed,
  SprintList,
  Task,
  TaskChanges,
  TaskList,
  LinearState,
  LinearTeam,
  SlackState,
  TasksSettings,
  Template,
  ShareBody,
  ShareCreated,
  ShareList,
  Team,
  TeamChanges,
  TeamList,
  TeamSummary,
  TeamInvite,
  TeamInvitesCreated,
  TeamMemberChanges,
  TeamMembers,
  InvitePreview,
  InviteRole,
  TemplateInput,
  Term,
  Translation,
  TranslationLanguage,
} from "./types";

// The one error every API call can end in. FastAPI answers every failure under /api/ as
// {"error": {"code", "message", "fields?"}} (docs/fe-integration-guide-owner-app.md section 4);
// anything else (a gateway timeout, a proxy error) becomes a `gateway_error`, and a request that
// never got an answer becomes `network_error`. Switch on `code`, never on `message`.
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: FieldError[];

  constructor(status: number, code: string, message: string, fields: FieldError[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
  }

  // The `message` of these is written for the owner. The others (404, 405, 422) are developer text.
  get isShowable(): boolean {
    return ![404, 405, 422].includes(this.status);
  }
}

export const GATEWAY_MESSAGE = "The server did not answer in time. Check again in a minute.";
export const NETWORK_MESSAGE = "Could not reach the server. Check your connection and try again.";
export const FALLBACK_MESSAGE = "Something went wrong. Please try again.";
// nginx answers the account routes with its own 429 page (not our JSON) past about 11 calls in a
// moment from one address (docs/fe-integration-guide-pilot-accounts.md section 4).
export const RATE_LIMIT_MESSAGE = "Too many attempts. Try again later.";

export type ApiOptions = {
  // "" in the browser (same origin); FastAPI's internal URL on the server.
  baseUrl?: string;
  fetch?: typeof fetch;
  // Extra headers for every request: the visitor's Cookie (and address) on the server.
  headers?: () => HeadersInit | Promise<HeadersInit>;
  // Called once for any 401 `not_authenticated`, before the error is thrown. The browser sends
  // the visitor to /login here; the server redirects in lib/api/server.ts instead.
  onUnauthorized?: () => void;
  // Sees every response before it is read; the logout route handler uses it to pass FastAPI's
  // Set-Cookie on to the browser.
  onResponse?: (response: Response) => void;
  // The team the caller is working on, sent as `X-Team-Id` on the team-scoped routes (and only those:
  // see `isTeamScoped`). null or undefined: send none, and the API uses the person's default team.
  // The browser reads it from the tab (lib/active-team.ts); a server page passes the `?team=` it was
  // opened with. Nothing is kept on the server between requests.
  teamId?: () => number | null | undefined;
  // Called once when a request that named a team is answered 404 "No such team.": the person is no
  // longer in it. The browser forgets the team and reloads on the default one, with a notice.
  onTeamGone?: () => void;
};

type Query = Record<string, string | number | undefined>;
// `team` names the team of this one call (null: none) in place of `teamId`'s: the meeting page asks
// about the team the MEETING belongs to, whatever the tab's team is.
type Call = { body?: unknown; query?: Query; write?: boolean; accept?: string; team?: number | null };

export const TEAM_HEADER = "X-Team-Id";
export const TEAM_NOT_FOUND_MESSAGE = "No such team.";

// The routes that read `X-Team-Id` (docs/fe-integration-guide-teams.md section 10): the team's own
// routes, tasks (list and create), projects, milestones, sprints, series (list and create), the
// integrations and a meeting's milestones. Joining by invitation names its team in the token, a
// task, a series or a meeting is read through its own team, so those go without.
export function isTeamScoped(path: string): boolean {
  const p = path.split("?", 1)[0];
  if (/^\/api\/team\/invites\/(preview|accept)$/.test(p)) return false;
  return (
    /^\/api\/team(\/|$)/.test(p) ||
    /^\/api\/(projects|milestones|sprints|integrations)(\/|$)/.test(p) ||
    /^\/api\/(tasks|series)$/.test(p) ||
    /^\/api\/meetings\/\d+\/milestones$/.test(p)
  );
}

export type Api = ReturnType<typeof createApi>;

export type DownloadedFile = { blob: Blob; filename: string | null };

// The file name in a Content-Disposition header: the UTF-8 `filename*` (RFC 5987) when there is
// one, else the plain `filename`. null when there is neither; the caller then names the file.
export function filenameFrom(header: string | null): string | null {
  if (!header) return null;
  const extended = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header);
  if (extended) {
    try {
      return decodeURIComponent(extended[1].trim());
    } catch {
      // fall through to the plain name
    }
  }
  const plain = /filename\s*=\s*"([^"]*)"/i.exec(header) ?? /filename\s*=\s*([^;]+)/i.exec(header);
  return plain ? plain[1].trim() : null;
}

export function createApi(options: ApiOptions = {}) {
  const base = (options.baseUrl ?? "").replace(/\/$/, "");
  const doFetch = options.fetch ?? ((...args: Parameters<typeof fetch>) => fetch(...args));
  let csrf: string | null = null;
  // The token the last write went out with, to tell a stale one from a session that never changed.
  let sentCsrf: string | null = null;

  // Which team each response's request named, for `fail` to tell "that team is gone" from "no such task".
  const teamSent = new WeakMap<Response, number>();

  async function send(method: string, path: string, { body, query, write, accept, team }: Call): Promise<Response> {
    const search = query
      ? Object.entries(query)
          .filter(([, v]) => v !== undefined)
          .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
          .join("&")
      : "";
    const headers: Record<string, string> = {
      Accept: accept ?? "application/json",
      ...((await options.headers?.()) as Record<string, string> | undefined),
    };
    const teamId = isTeamScoped(path) ? (team !== undefined ? team : (options.teamId?.() ?? null)) : null;
    if (teamId !== null) headers[TEAM_HEADER] = String(teamId);
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (write) {
      sentCsrf = csrf ?? (await readSession()).csrf_token;
      headers["X-CSRF-Token"] = sentCsrf;
    }
    let res: Response;
    try {
      res = await doFetch(`${base}${path}${search ? `?${search}` : ""}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
        redirect: "manual",
      });
    } catch {
      throw new ApiError(0, "network_error", NETWORK_MESSAGE);
    }
    if (teamId !== null) teamSent.set(res, teamId);
    options.onResponse?.(res);
    return res;
  }

  async function fail(res: Response): Promise<never> {
    const type = res.headers.get("content-type") ?? "";
    if (!type.includes("application/json")) {
      if (res.status === 429) throw new ApiError(429, "too_many_requests", RATE_LIMIT_MESSAGE);
      throw new ApiError(res.status, "gateway_error", GATEWAY_MESSAGE);
    }
    const data = (await res.json().catch(() => null)) as {
      error?: { code?: string; message?: string; fields?: FieldError[] | null };
    } | null;
    const error = data?.error;
    if (!error?.code) throw new ApiError(res.status, "gateway_error", GATEWAY_MESSAGE);
    if (res.status === 401 && error.code === "not_authenticated") options.onUnauthorized?.();
    // The one 404 that is about the team and not the thing asked for: the code is the same
    // (`not_found`), so the words tell them apart (guide section 10).
    if (res.status === 404 && error.code === "not_found" && error.message === TEAM_NOT_FOUND_MESSAGE && teamSent.has(res)) {
      options.onTeamGone?.();
    }
    throw new ApiError(res.status, error.code, error.message ?? FALLBACK_MESSAGE, error.fields ?? []);
  }

  async function request<T>(method: string, path: string, call: Call = {}): Promise<T> {
    let res = await send(method, path, call);
    // A stale or missing CSRF token is a 403 `csrf_rejected`: read a fresh one and retry once.
    if (call.write && res.status === 403) {
      const type = res.headers.get("content-type") ?? "";
      const data = type.includes("application/json") ? await res.clone().json().catch(() => null) : null;
      if (data?.error?.code === "csrf_rejected") {
        csrf = null;
        const used = sentCsrf;
        const fresh = await readSession();
        // Retry for a signed-in visitor, and for a logged-out one whose token really changed (a login
        // or logout in another tab): a sign-up or reset then does not fail with the typed text kept.
        // The same token again would only be refused again.
        if (fresh.authenticated || fresh.csrf_token !== used) res = await send(method, path, call);
      }
    }
    if (!res.ok) return fail(res);
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  // A file the API builds for this request (the notes as PDF or Word): the bytes and the name the
  // server gave it. Failures are the usual JSON errors, so they throw the same `ApiError`.
  async function download(path: string, query: Query): Promise<DownloadedFile> {
    const res = await send("GET", path, { query, accept: "*/*" });
    if (!res.ok) return fail(res);
    const type = res.headers.get("content-type") ?? "";
    // A 200 that is not a file (a proxy's page) is a failure, not something to save.
    if (type.includes("text/html") || type.includes("application/json")) {
      throw new ApiError(res.status, "gateway_error", GATEWAY_MESSAGE);
    }
    return { blob: await res.blob(), filename: filenameFrom(res.headers.get("content-disposition")) };
  }

  // GET /api/session: whether the visitor is signed in (and as whom), and the token that goes with the cookie.
  async function readSession(): Promise<SessionState> {
    const state = await request<SessionState>("GET", "/api/session");
    csrf = state.csrf_token;
    return state;
  }

  return {
    session: readSession,
    // The token changes at login and at logout; both responses carry the new one.
    async login(email: string, password: string): Promise<SessionState> {
      const state = await request<SessionState>("POST", "/api/session", {
        body: { email, password },
        write: true,
      });
      csrf = state.csrf_token;
      return state;
    },
    async logout(): Promise<SessionState> {
      const state = await request<SessionState>("DELETE", "/api/session", { write: true });
      csrf = state.csrf_token;
      return state;
    },
    // Public account routes: no session needed, but every write still sends the CSRF token
    // (docs/fe-integration-guide-pilot-accounts.md section 0).
    pilot: () => request<Pilot>("GET", "/api/pilot"),
    signup: (body: SignUpBody) => request<{ status: "check_your_email" }>("POST", "/api/signup", { body, write: true }),
    // The "Stop mail from Beyric Note Taker" page (public; the token is the mail's footer link).
    optOutOfMail: (token: string) =>
      request<{ status: "unsubscribed" }>("POST", "/api/mail/opt-out", { body: { token }, write: true }),
    resendVerification: (email: string) =>
      request<{ status: "check_your_email" }>("POST", "/api/signup/resend", { body: { email }, write: true }),
    verifyEmail: (token: string, password: string) =>
      request<{ status: "verified" }>("POST", "/api/verify-email", { body: { token, password }, write: true }),
    forgotPassword: (email: string) =>
      request<{ status: "check_your_email" }>("POST", "/api/password/forgot", { body: { email }, write: true }),
    resetPassword: (token: string, password: string) =>
      request<{ status: "password_changed" }>("POST", "/api/password/reset", { body: { token, password }, write: true }),
    joinWaitlist: (email: string, name?: string) =>
      request<{ status: "on_the_list" }>("POST", "/api/waitlist", {
        body: name ? { email, name } : { email },
        write: true,
      }),
    meetings: {
      // `from` and `to` (YYYY-MM-DD, both included, the caller's own days) keep a range
      // (docs/fe-integration-guide-owner-app.md 5.1a); with neither the list is as it always was. `tz` is
      // the zone of those days when the caller has none stored (the browser's own).
      list: (query: { limit?: number; before_id?: number; from?: string; to?: string; tz?: string } = {}) =>
        request<MeetingList>("GET", "/api/meetings", { query }),
      get: (id: number) => request<Meeting>("GET", `/api/meetings/${id}`),
      create: (body: NewMeeting) => request<Meeting>("POST", "/api/meetings", { body, write: true }),
      // 204 and no body. 409 `meeting_active` (bot in the call or notes being written), 502
      // `recording_delete_failed` (nothing deleted); a repeat is 404 (docs/fe-integration-guide-meeting-delete.md).
      remove: (id: number) => request<void>("DELETE", `/api/meetings/${id}`, { write: true }),
      // "Share with team": the answer is the whole meeting (owner only; anyone else gets 404).
      // Recurring meetings (docs/fe-integration-guide-series.md): put the caller's own meeting into a
      // series of their team, or out (`null`); "Make this a recurring meeting?" makes one from a link
      // that was used before (409 `not_reused`).
      setSeries: (id: number, series_id: number | null) =>
        request<MeetingSeriesResult>("PUT", `/api/meetings/${id}/series`, { body: { series_id }, write: true }),
      makeRecurring: (id: number) => request<MeetingSeriesResult>("POST", `/api/meetings/${id}/recurring`, { write: true }),
      // `teamId` names the team to share with (required once a person is on two teams: 422 `team_required`
      // without it). Moving gives the meeting to the team (409 `meeting_not_ready`, `personal_team`,
      // `already_moved`; 404 for a team that is not theirs); moving back is for the owning team's owner or
      // admin (403 `team_forbidden`, 409 `not_moved`, `recorder_gone`). docs/fe-integration-guide-teams.md 4 to 6.
      share: (id: number, teamId?: number) =>
        request<Meeting>("POST", `/api/meetings/${id}/share`, {
          write: true,
          ...(teamId !== undefined ? { body: { team_id: teamId } } : {}),
        }),
      move: (id: number, teamId: number) =>
        request<Meeting>("POST", `/api/meetings/${id}/move`, { body: { team_id: teamId }, write: true }),
      moveBack: (id: number) => request<Meeting>("POST", `/api/meetings/${id}/move-back`, { write: true }),
      unshare: (id: number) => request<Meeting>("DELETE", `/api/meetings/${id}/share`, { write: true }),
      cancel: (id: number) => request<Meeting>("POST", `/api/meetings/${id}/cancel`, { write: true }),
      end: (id: number) => request<Meeting>("POST", `/api/meetings/${id}/end`, { write: true }),
      retry: (id: number) => request<Meeting>("POST", `/api/meetings/${id}/retry`, { write: true }),
      regenerate: (id: number, template_id: number) =>
        request<Meeting>("POST", `/api/meetings/${id}/regenerate`, { body: { template_id }, write: true }),
      // The notes as a PDF or Word file, built when asked and not stored (GET .../export).
      // `language` fr or es builds it from the finished translation (409 translation_missing
      // otherwise); `en` is the original.
      export: (id: number, format: ExportFormat, transcript: boolean, language: NoteLanguage = "en") =>
        download(`/api/meetings/${id}/export`, { format, transcript: String(transcript), language }),
      // Translate the notes and transcript (docs/fe-integration-guide-translate.md). `request`
      // answers the translation: ready (cached) or translating (ask `get` again in 2 seconds).
      // Share notes by email (docs/fe-integration-guide-share.md). `create` answers 202 with
      // `queued` or `waiting_translation`; `list` is what "Shared with ..." reads (owner only).
      shares: {
        create: (id: number, body: ShareBody) =>
          request<ShareCreated>("POST", `/api/meetings/${id}/shares`, { body, write: true }),
        list: async (id: number) => (await request<ShareList>("GET", `/api/meetings/${id}/shares`)).shares,
      },
      translations: {
        request: (id: number, language: TranslationLanguage) =>
          request<Translation>("POST", `/api/meetings/${id}/translations`, { body: { language }, write: true }),
        get: (id: number, language: TranslationLanguage) =>
          request<Translation>("GET", `/api/meetings/${id}/translations/${language}`),
        remove: (id: number, language: TranslationLanguage) =>
          request<void>("DELETE", `/api/meetings/${id}/translations/${language}`, { write: true }),
      },
    },
    templates: {
      list: async () => (await request<{ templates: Template[] }>("GET", "/api/templates")).templates,
      create: (body: TemplateInput) => request<Template>("POST", "/api/templates", { body, write: true }),
      update: (id: number, body: TemplateInput) =>
        request<Template>("PUT", `/api/templates/${id}`, { body, write: true }),
      // 204 and no body. 409 `last_template` for the only one (its message is for the person); a
      // second delete of the same template is a 404.
      remove: (id: number) => request<void>("DELETE", `/api/templates/${id}`, { write: true }),
    },
    // Ask Beyric: `scope` is the counts and the suggestion chips; `ask` answers one question (422
    // `question_invalid`, 429 `ask_limit`, 502 `ask_failed` carry a message written for the person).
    ask: {
      scope: () => request<AskScope>("GET", "/api/ask/scope"),
      ask: (question: string) => request<AskAnswer>("POST", "/api/ask", { body: { question }, write: true }),
    },
    // GET /api/decisions groups by meeting; `limit` counts meetings; `status` is one tab. Hiding never
    // edits the notes. `update` is the owner's only (403 `not_owner`, 404 for a reader of nothing, 422
    // `replaced_by_*`) and answers the whole decision (docs/fe-integration-guide-decisions.md).
    decisions: {
      list: (query: { q?: string; status?: DecisionStatus; limit?: number; before_id?: number } = {}) =>
        request<DecisionList>("GET", "/api/decisions", { query }),
      update: (id: number, body: DecisionChanges) =>
        request<Decision>("PATCH", `/api/decisions/${id}`, { body, write: true }),
      hide: (id: number) => request<void>("POST", `/api/decisions/${id}/hide`, { write: true }),
    },
    // Tasks (docs/fe-integration-guide-tasks.md). `list` is the board: `assignee` is me, none or a
    // user id. `update` sends only the fields that change and answers the whole task; a change the
    // caller may not make is 403 `team_forbidden` (nothing applied), a task they cannot see is 404.
    // Deleting answers 204. `forMeeting` is the meeting page's Action items.
    tasks: {
      list: (
        query: { assignee?: string; status?: string; suggested?: boolean; project?: string; milestone?: number; sprint?: string; limit?: number; before_id?: number } = {},
      ) =>
        request<TaskList>("GET", "/api/tasks", {
          query: { ...query, suggested: query.suggested === undefined ? undefined : String(query.suggested) },
        }),
      get: (id: number) => request<Task>("GET", `/api/tasks/${id}`),
      create: (body: Partial<NewTask> = {}) => request<Task>("POST", "/api/tasks", { body, write: true }),
      update: (id: number, body: TaskChanges) =>
        request<Task>("PATCH", `/api/tasks/${id}`, { body, write: true }),
      remove: (id: number) => request<void>("DELETE", `/api/tasks/${id}`, { write: true }),
      // Suggested tasks (docs/fe-integration-guide-suggested-tasks.md): `list({ suggested: true })` is
      // the Suggested tasks section (the default list leaves them out); `accept` answers the task,
      // `remove` is Dismiss. A task already accepted answers 200 again; 404 means it is gone.
      accept: (id: number) => request<Task>("POST", `/api/tasks/${id}/accept`, { write: true }),
      forMeeting: (meetingId: number) => request<MeetingTasks>("GET", `/api/meetings/${meetingId}/tasks`),
    },
    // Projects and milestones (docs/fe-integration-guide-projects.md). Everyone on the team reads;
    // an owner or admin writes (403 `team_forbidden` otherwise). `update` sends one field.
    projects: {
      list: () => request<ProjectList>("GET", "/api/projects"),
      create: (body: { name?: string; color?: string; keywords?: string[] } = {}) =>
        request<Project>("POST", "/api/projects", { body, write: true }),
      update: (id: number, body: ProjectChanges) =>
        request<Project>("PATCH", `/api/projects/${id}`, { body, write: true }),
      remove: (id: number) => request<void>("DELETE", `/api/projects/${id}`, { write: true }),
    },
    // Sprints (docs/fe-integration-guide-sprints.md). Everyone on the team reads; an owner or admin
    // closes the current one (403 `team_forbidden` otherwise, 409 `sprint_not_current` when it is
    // already closed or is not the current one). `list` with a project id is that project's own pair
    // when it has a sprint length of its own, else the team's pair counting only its tasks.
    sprints: {
      list: (project?: number) => request<SprintList>("GET", "/api/sprints", { query: { project } }),
      close: (id: number) => request<SprintClosed>("POST", `/api/sprints/${id}/close`, { body: {}, write: true }),
    },
    milestones: {
      list: () => request<MilestoneList>("GET", "/api/milestones"),
      forMeeting: (meetingId: number, team?: number | null) =>
        request<MeetingMilestones>("GET", `/api/meetings/${meetingId}/milestones`, { team }),
      confirm: (id: number) => request<Milestone>("POST", `/api/milestones/${id}/confirm`, { body: {}, write: true }),
    },
    // The teams a person belongs to (docs/fe-integration-guide-teams.md section 9): the switcher's list and
    // "Create a team" (409 `team_limit` at three owned teams, 422 `invalid_name`, 409 `multi_team_disabled`).
    teams: {
      list: () => request<TeamList>("GET", "/api/teams"),
      create: (name: string) => request<TeamSummary>("POST", "/api/teams", { body: { name }, write: true }),
    },
    // The Team screen (docs/fe-integration-guide-team.md). A member is addressed by `user_id`.
    // `team` on a call names the team it is about in place of the tab's (the meeting page: the meeting's).
    team: {
      get: (team?: number | null) => request<Team>("GET", "/api/team", { team }),
      update: (body: TeamChanges) => request<Team>("PATCH", "/api/team", { body, write: true }),
      members: (team?: number | null) => request<TeamMembers>("GET", "/api/team/members", { team }),
      updateMember: (userId: number, body: TeamMemberChanges) =>
        request<TeamMembers>("PATCH", `/api/team/members/${userId}`, { body, write: true }),
      removeMember: (userId: number) => request<void>("DELETE", `/api/team/members/${userId}`, { write: true }),
      invite: (invitees: Array<{ email: string; name?: string | null }>, role: InviteRole) =>
        request<TeamInvitesCreated>("POST", "/api/team/invites", { body: { invitees, role }, write: true }),
      resend: (id: number) => request<TeamInvite>("POST", `/api/team/invites/${id}/resend`, { write: true }),
      revoke: (id: number) => request<void>("DELETE", `/api/team/invites/${id}`, { write: true }),
      preview: (token: string) => request<InvitePreview>("GET", "/api/team/invites/preview", { query: { token } }),
      accept: (token: string) => request<Team>("POST", "/api/team/invites/accept", { body: { token }, write: true }),
    },
    // Items to check against the transcript. Adding a term also marks the item checked.
    review: {
      list: (state: ReviewState = "open") => request<ReviewList>("GET", "/api/review", { query: { state } }),
      check: (id: number) => request<ReviewItem>("POST", `/api/review/${id}/check`, { write: true }),
      uncheck: (id: number) => request<ReviewItem>("POST", `/api/review/${id}/uncheck`, { write: true }),
      addToVocabulary: (id: number, term: string) =>
        request<ReviewTermResult>("POST", `/api/review/${id}/vocabulary`, { body: { term }, write: true }),
    },
    // Whether the notes-ready email is on (GET and PUT /api/settings/email).
    settings: {
      // The language meetings open in when a translation exists (GET and PUT /api/settings/language).
      language: {
        get: () => request<LanguagePreferences>("GET", "/api/settings/language"),
        set: (default_language: NoteLanguage) =>
          request<LanguagePreferences>("PUT", "/api/settings/language", { body: { default_language }, write: true }),
      },
      // The one-time recording-consent card on the Meetings page (POST /api/settings/recording-ack).
      recordingAck: () => request<RecordingAck>("POST", "/api/settings/recording-ack", { write: true }),
      // The person's own time zone (GET and PUT /api/settings/time-zone).
      timeZone: {
        get: () => request<TimeZonePreferences>("GET", "/api/settings/time-zone"),
        set: (time_zone: string) =>
          request<TimeZonePreferences>("PUT", "/api/settings/time-zone", { body: { time_zone }, write: true }),
      },
      // The three switches of the Tasks card (GET and PUT /api/settings/tasks); all are sent together.
      tasks: {
        get: () => request<TasksSettings>("GET", "/api/settings/tasks"),
        set: (body: TasksSettings) => request<TasksSettings>("PUT", "/api/settings/tasks", { body, write: true }),
      },
      // The person's own agenda and reminder mail switch (GET and PUT /api/settings/reminders).
      reminders: {
        get: () => request<ReminderPreferences>("GET", "/api/settings/reminders"),
        set: (notify_reminders: boolean) =>
          request<ReminderPreferences>("PUT", "/api/settings/reminders", { body: { notify_reminders }, write: true }),
      },
      email: {
        get: () => request<EmailPreferences>("GET", "/api/settings/email"),
        set: (notify_email: boolean) =>
          request<EmailPreferences>("PUT", "/api/settings/email", { body: { notify_email }, write: true }),
      },
    },
    // Recurring meetings (docs/fe-integration-guide-series.md). A series of another team is 404.
    // `sendAgenda` mails the teammates who were in the last three meetings (409 `no_attendees`,
    // `nothing_to_send`; 429 `agenda_rate_limited`). `question` is "On next agenda" and "Mark resolved".
    series: {
      // `team`: the meeting page lists and makes series in the MEETING's team (a meeting joins only a series of its own team).
      list: (team?: number | null) => request<SeriesList>("GET", "/api/series", { team }),
      create: (title: string, team?: number | null) =>
        request<SeriesCard>("POST", "/api/series", { body: { title }, write: true, team }),
      get: (id: number) => request<SeriesPage>("GET", `/api/series/${id}`),
      // Link a recurring meeting to a project, or unlink it (`null`): the Projects screen's Recurring
      // meetings checklist (docs/fe-integration-guide-series-project-link.md). Owner or admin only.
      linkProject: (id: number, projectId: number | null) =>
        request<SeriesCard>("PUT", `/api/series/${id}/project`, { body: { project_id: projectId }, write: true }),
      addAgenda: (id: number, text: string) =>
        request<AgendaItem>("POST", `/api/series/${id}/agenda`, { body: { text }, write: true }),
      removeAgenda: (id: number, itemId: number) =>
        request<void>("DELETE", `/api/series/${id}/agenda/${itemId}`, { write: true }),
      sendAgenda: (id: number) => request<AgendaSent>("POST", `/api/series/${id}/agenda/send`, { write: true }),
    },
    questions: {
      update: (id: number, body: QuestionChanges) =>
        request<OpenQuestion>("PATCH", `/api/questions/${id}`, { body, write: true }),
    },
    // The bell: what the app has told the user, newest first. `readAll` answers the new unread count.
    notifications: {
      list: (query: { limit?: number; before_id?: number } = {}) =>
        request<NoticeList>("GET", "/api/notifications", { query }),
      readAll: () => request<{ unread_notifications: number }>("POST", "/api/notifications/read-all", { write: true }),
    },
    // Calendar (docs/fe-integration-guide-calendar-settings.md). `get` answers 404
    // `calendar_unavailable` while the feature is off. `connect` returns the consent URL of the
    // provider (Google, or Microsoft Outlook when `microsoft_enabled`) to navigate to; the browser
    // comes back to /settings with the result in the query string.
    // `settings` changes the auto-join rule and/or the template for calendar meetings and answers
    // with the whole state; `override` is the switch on one Upcoming row (true joins, false does
    // not, null clears it) and answers with that row, or 409 `not_joinable` with the reason.
    calendar: {
      get: () => request<CalendarState>("GET", "/api/calendar"),
      connect: (provider: CalendarProvider = "google") =>
        request<ConnectStart>("POST", `/api/calendar/${provider}/connect`, {
          body: { features: ["calendar"], return_to: "/settings" },
          write: true,
        }),
      disconnect: (provider: CalendarProvider = "google") =>
        request<DisconnectResult>("POST", `/api/calendar/${provider}/disconnect`, { write: true }),
      // The Recurring cards. `timeZone` is the browser's own zone: the texts come back written in it.
      recurring: (timeZone?: string) =>
        request<RecurringState>("GET", "/api/calendar/recurring", { query: timeZone ? { tz: timeZone } : {} }),
      settings: (body: CalendarSettingsInput) =>
        request<CalendarState>("PUT", "/api/calendar/settings", { body, write: true }),
      override: (eventId: number, override: boolean | null) =>
        request<CalendarEvent>("PUT", `/api/calendar/events/${eventId}/override`, {
          body: { override },
          write: true,
        }),
      // "Not recorded" (docs/fe-integration-guide-calendar-send-now.md): the last seven days'
      // meetings the note taker did not get into. `sendNow` takes a row's `join_id` (not an event
      // id) and sends the note taker through the full pre-flight: 200 with the new meeting, or a
      // 409 / 403 / 502 with the reason in `message` (`bot_busy`, `hours_cap_reached`,
      // `meeting_ended`, `cannot_send_now`, `bot_send_failed`, ...) and nothing changed.
      notRecorded: () => request<NotRecordedList>("GET", "/api/not-recorded"),
      sendNow: (joinId: number) =>
        request<SendNowResult>("POST", `/api/scheduled-joins/${joinId}/send-now`, { write: true }),
    },
    // The team's integrations (W10): the Slack webhook. The address goes up on connect and never comes back.
    integrations: {
      slack: {
        get: () => request<SlackState>("GET", "/api/integrations/slack"),
        connect: (webhook_url: string) =>
          request<SlackState>("PUT", "/api/integrations/slack", { body: { webhook_url }, write: true }),
        disconnect: () => request<void>("DELETE", "/api/integrations/slack", { write: true }),
      },
      // Linear, the tracker: list the teams a key can reach, then connect with one of them.
      linear: {
        get: () => request<LinearState>("GET", "/api/integrations/linear"),
        teams: (api_key: string) =>
          request<{ teams: LinearTeam[] }>("POST", "/api/integrations/linear/teams", { body: { api_key }, write: true }),
        connect: (api_key: string, team_id: string) =>
          request<LinearState>("PUT", "/api/integrations/linear", { body: { api_key, team_id }, write: true }),
        disconnect: () => request<void>("DELETE", "/api/integrations/linear", { write: true }),
      },
    },
    vocabulary: {
      list: async () => (await request<{ terms: Term[] }>("GET", "/api/vocabulary")).terms,
      add: (term: string) => request<Term>("POST", "/api/vocabulary", { body: { term }, write: true }),
      remove: (id: number) => request<void>("DELETE", `/api/vocabulary/${id}`, { write: true }),
    },
  };
}

// What to show the owner for any thrown value.
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "csrf_rejected") return "Your session changed. Reload the page and try again.";
    return error.isShowable ? error.message : FALLBACK_MESSAGE;
  }
  return FALLBACK_MESSAGE;
}
