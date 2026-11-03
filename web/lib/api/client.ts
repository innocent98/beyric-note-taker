import type {
  FieldError,
  Meeting,
  MeetingList,
  NewMeeting,
  SessionState,
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

export type ApiOptions = {
  // "" in the browser (same origin); FastAPI's internal URL on the server.
  baseUrl?: string;
  fetch?: typeof fetch;
  // Extra headers for every request: the visitor's Cookie (and address) on the server.
  headers?: () => HeadersInit | Promise<HeadersInit>;
  // Called once for any 401 `not_authenticated`, before the error is thrown. The browser sends
  // the visitor to /login here; the server redirects in lib/api/server.ts instead.
  onUnauthorized?: () => void;
};

type Query = Record<string, string | number | undefined>;
type Call = { body?: unknown; query?: Query; write?: boolean };

export type Api = ReturnType<typeof createApi>;

export function createApi(options: ApiOptions = {}) {
  const base = (options.baseUrl ?? "").replace(/\/$/, "");
  const doFetch = options.fetch ?? ((...args: Parameters<typeof fetch>) => fetch(...args));
  let csrf: string | null = null;

  async function send(method: string, path: string, { body, query, write }: Call): Promise<Response> {
    const search = query
      ? Object.entries(query)
          .filter(([, v]) => v !== undefined)
          .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
          .join("&")
      : "";
    const headers: Record<string, string> = {
      Accept: "application/json",
      ...((await options.headers?.()) as Record<string, string> | undefined),
    };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (write) headers["X-CSRF-Token"] = csrf ?? (await readSession()).csrf_token;
    try {
      return await doFetch(`${base}${path}${search ? `?${search}` : ""}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: "no-store",
        redirect: "manual",
      });
    } catch {
      throw new ApiError(0, "network_error", NETWORK_MESSAGE);
    }
  }

  async function fail(res: Response): Promise<never> {
    const type = res.headers.get("content-type") ?? "";
    if (!type.includes("application/json")) {
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
        const fresh = await readSession();
        if (fresh.authenticated) res = await send(method, path, call);
      }
    }
    if (!res.ok) return fail(res);
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  // GET /api/session: whether the visitor is the owner, and the token that goes with the cookie.
  async function readSession(): Promise<SessionState> {
    const state = await request<SessionState>("GET", "/api/session");
    csrf = state.csrf_token;
    return state;
  }

  return {
    session: readSession,
    // The token changes at login and at logout; both responses carry the new one.
    async login(password: string): Promise<SessionState> {
      const state = await request<SessionState>("POST", "/api/session", { body: { password }, write: true });
      csrf = state.csrf_token;
      return state;
    },
    async logout(): Promise<SessionState> {
      const state = await request<SessionState>("DELETE", "/api/session", { write: true });
      csrf = state.csrf_token;
      return state;
    },
    meetings: {
      list: (query: { limit?: number; before_id?: number } = {}) =>
        request<MeetingList>("GET", "/api/meetings", { query }),
      get: (id: number) => request<Meeting>("GET", `/api/meetings/${id}`),
      create: (body: NewMeeting) => request<Meeting>("POST", "/api/meetings", { body, write: true }),
      cancel: (id: number) => request<Meeting>("POST", `/api/meetings/${id}/cancel`, { write: true }),
      retry: (id: number) => request<Meeting>("POST", `/api/meetings/${id}/retry`, { write: true }),
      regenerate: (id: number, template_id: number) =>
        request<Meeting>("POST", `/api/meetings/${id}/regenerate`, { body: { template_id }, write: true }),
    },
    templates: {
      list: async () => (await request<{ templates: Template[] }>("GET", "/api/templates")).templates,
      create: (body: TemplateInput) => request<Template>("POST", "/api/templates", { body, write: true }),
      update: (id: number, body: TemplateInput) =>
        request<Template>("PUT", `/api/templates/${id}`, { body, write: true }),
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
