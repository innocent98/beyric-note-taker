import type {
  DecisionList,
  ExportFormat,
  FieldError,
  Meeting,
  MeetingList,
  NewMeeting,
  Pilot,
  ReviewItem,
  ReviewList,
  ReviewState,
  ReviewTermResult,
  SessionState,
  SignUpBody,
  Template,
  TemplateInput,
  Term,
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
};

type Query = Record<string, string | number | undefined>;
type Call = { body?: unknown; query?: Query; write?: boolean; accept?: string };

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

  async function send(method: string, path: string, { body, query, write, accept }: Call): Promise<Response> {
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
      list: (query: { limit?: number; before_id?: number } = {}) =>
        request<MeetingList>("GET", "/api/meetings", { query }),
      get: (id: number) => request<Meeting>("GET", `/api/meetings/${id}`),
      create: (body: NewMeeting) => request<Meeting>("POST", "/api/meetings", { body, write: true }),
      cancel: (id: number) => request<Meeting>("POST", `/api/meetings/${id}/cancel`, { write: true }),
      end: (id: number) => request<Meeting>("POST", `/api/meetings/${id}/end`, { write: true }),
      retry: (id: number) => request<Meeting>("POST", `/api/meetings/${id}/retry`, { write: true }),
      regenerate: (id: number, template_id: number) =>
        request<Meeting>("POST", `/api/meetings/${id}/regenerate`, { body: { template_id }, write: true }),
      // The notes as a PDF or Word file, built when asked and not stored (GET .../export).
      export: (id: number, format: ExportFormat, transcript: boolean) =>
        download(`/api/meetings/${id}/export`, { format, transcript: String(transcript) }),
    },
    templates: {
      list: async () => (await request<{ templates: Template[] }>("GET", "/api/templates")).templates,
      create: (body: TemplateInput) => request<Template>("POST", "/api/templates", { body, write: true }),
      update: (id: number, body: TemplateInput) =>
        request<Template>("PUT", `/api/templates/${id}`, { body, write: true }),
    },
    // GET /api/decisions groups by meeting; `limit` counts meetings. Hiding never edits the notes.
    decisions: {
      list: (query: { q?: string; limit?: number; before_id?: number } = {}) =>
        request<DecisionList>("GET", "/api/decisions", { query }),
      hide: (id: number) => request<void>("POST", `/api/decisions/${id}/hide`, { write: true }),
    },
    // Items to check against the transcript. Adding a term also marks the item checked.
    review: {
      list: (state: ReviewState = "open") => request<ReviewList>("GET", "/api/review", { query: { state } }),
      check: (id: number) => request<ReviewItem>("POST", `/api/review/${id}/check`, { write: true }),
      uncheck: (id: number) => request<ReviewItem>("POST", `/api/review/${id}/uncheck`, { write: true }),
      addToVocabulary: (id: number, term: string) =>
        request<ReviewTermResult>("POST", `/api/review/${id}/vocabulary`, { body: { term }, write: true }),
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
