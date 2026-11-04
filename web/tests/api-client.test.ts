import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { ApiError, createApi, errorMessage, GATEWAY_MESSAGE, NETWORK_MESSAGE } from "@/lib/api/client";
import { capturedDelete, read, REPO } from "./helpers";

// The client is tested against the responses stream B captured from the running API
// (docs/fe-captures/owner-app/), replayed byte for byte, so no shape here is a guess.

type Capture = { request: { method: string; path: string }; response: { status: number; headers: Record<string, string>; body: unknown } };

function capture(name: string): Capture {
  return JSON.parse(read(join(REPO, "docs/fe-captures/owner-app", name)));
}

function respond(c: Capture): Response {
  const { status, headers, body } = c.response;
  const text = status === 204 ? null : typeof body === "string" ? body : JSON.stringify(body);
  return new Response(text, { status, headers });
}

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>;

function setup(handler: Handler, extra: Partial<Parameters<typeof createApi>[0]> = {}) {
  const calls: { url: string; init: RequestInit }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    calls.push({ url: String(input), init });
    return handler(String(input), init);
  });
  const api = createApi({ fetch: fetchMock as unknown as typeof fetch, ...extra });
  return { api, calls };
}

const anonymous = () => respond(capture("01-session-anonymous.json"));
const header = (init: RequestInit, name: string) => (init.headers as Record<string, string>)[name];

describe("api client", () => {
  it("reads the session and the CSRF token", async () => {
    const { api } = setup(() => anonymous());
    expect(await api.session()).toEqual({
      authenticated: false,
      csrf_token: "<csrf-A>",
      user: null,
      usage: null,
      unread_notifications: null,
    });
  });

  it("sends the token from GET /api/session on a write, fetching it only once", async () => {
    const { api, calls } = setup((url, init) =>
      url === "/api/session" && init.method === "GET"
        ? anonymous()
        : respond(capture("08-login-ok.json")),
    );
    const state = await api.login("owner@example.test", "pw");
    expect(state).toMatchObject({ authenticated: true, csrf_token: "<csrf-C>" });
    expect(state.user).toEqual({ email: "owner@example.test", name: null, is_admin: true, email_verified: true });
    expect(state.usage).toEqual({ used_seconds: 0, cap_seconds: null });
    expect(calls.map((c) => `${c.init.method} ${c.url}`)).toEqual(["GET /api/session", "POST /api/session"]);
    expect(header(calls[1].init, "X-CSRF-Token")).toBe("<csrf-A>");
    expect(JSON.parse(String(calls[1].init.body))).toEqual({ email: "owner@example.test", password: "pw" });
    expect(header(calls[1].init, "Content-Type")).toBe("application/json");

    // The login response carried a new token; the next write uses it without another GET.
    await api.logout().catch(() => undefined);
    expect(calls.filter((c) => c.init.method === "GET")).toHaveLength(1);
    expect(header(calls[2].init, "X-CSRF-Token")).toBe("<csrf-C>");
  });

  it("does not send a token on a read", async () => {
    const { api, calls } = setup(() => respond(capture("11-templates-seeded.json")));
    const templates = await api.templates.list();
    expect(templates.map((t) => t.name)).toEqual(["Client call", "General", "Interview", "Team update"]);
    expect(header(calls[0].init, "X-CSRF-Token")).toBeUndefined();
  });

  it("returns the wrapped lists and the full meeting detail", async () => {
    const { api } = setup((url) =>
      url.startsWith("/api/meetings/7")
        ? respond(capture("47-meeting-detail-ready.json"))
        : respond(capture("53-meetings-list.json")),
    );
    const list = await api.meetings.list({ limit: 50 });
    expect(list.worker_stale).toBe(false);
    expect(list.meetings[0].status_text).toBe("Failed");
    const meeting = await api.meetings.get(7);
    expect(meeting.notes_html).toContain("<h2>Summary</h2>");
    expect(meeting.can_regenerate).toBe(true);
  });

  it("puts query parameters on the list call", async () => {
    const { api, calls } = setup(() => respond(capture("55-meetings-list-page-2.json")));
    await api.meetings.list({ limit: 3, before_id: 8 });
    expect(calls[0].url).toBe("/api/meetings?limit=3&before_id=8");
  });

  it.each([
    ["05-login-wrong-password.json", 401, "invalid_credentials", "That email or password is not right."],
    ["23-template-duplicate-name.json", 400, "duplicate_name", "A template with that name already exists."],
    ["34-meeting-create-while-active.json", 409, "bot_already_active", "A bot is already on a meeting. Wait for it to finish first."],
    ["62-meeting-regenerate-openai-down.json", 502, "notes_failed", "The notes could not be written. The old notes were kept."],
    ["80-limit-sixth-attempt.json", 429, "too_many_attempts", "Too many failed attempts. Try again later."],
  ])("turns %s into an ApiError with the captured code and message", async (file, status, code, message) => {
    const c = capture(file);
    const { api } = setup((url, init) => (init.method === "GET" ? anonymous() : respond(c)));
    const error = await (c.request.path.startsWith("/api/session")
      ? api.login("owner@example.test", "x")
      : c.request.path.startsWith("/api/templates")
        ? api.templates.create({ name: "a", instructions: "b" })
        : c.request.path.endsWith("regenerate")
          ? api.meetings.regenerate(7, 1)
          : api.meetings.create({ url: "https://meet.google.com/abc-defg-hij", template_id: 1 })
    ).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status, code, message });
    expect(errorMessage(error)).toBe(message);
  });

  it("deletes a template: DELETE with the token, 204 and nothing to read", async () => {
    const ok = capturedDelete("07-delete.json");
    const { api, calls } = setup((url, init) => (init.method === "GET" ? anonymous() : respond(ok as unknown as Capture)));
    await expect(api.templates.remove(4)).resolves.toBeUndefined();
    const sent = calls.find((c) => c.init.method === "DELETE")!;
    expect(sent.url).toBe(ok.request.path);
    expect(header(sent.init, "X-CSRF-Token")).toBe("<csrf-A>");
  });

  it.each([
    ["25-delete-last.json", 409, "last_template", "Keep at least one template. Add another one first, then delete this."],
    ["12-delete-again.json", 404, "not_found", "No such template"],
  ])("turns the captured %s into an ApiError", async (file, status, code, message) => {
    const c = capturedDelete(file);
    const { api } = setup((url, init) => (init.method === "GET" ? anonymous() : respond(c as unknown as Capture)));
    const error = await api.templates.remove(1).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status, code, message });
    // 409 is written for the person; 404 is developer text and shows the plain fallback
    expect(errorMessage(error)).toBe(status === 409 ? message : "Something went wrong. Please try again.");
  });

  it("keeps the field list of a 422 and shows our own copy, not the developer text", async () => {
    const c = capture("06-login-missing-password.json");
    const { api } = setup((url, init) => (init.method === "GET" ? anonymous() : respond(c)));
    const error = (await api.login("", "").catch((e: unknown) => e)) as ApiError;
    expect(error.fields).toEqual([
      { field: "email", message: "Field required" },
      { field: "password", message: "Field required" },
    ]);
    expect(errorMessage(error)).toBe("Something went wrong. Please try again.");
  });

  it("calls onUnauthorized once on a 401 not_authenticated, and still throws", async () => {
    const onUnauthorized = vi.fn();
    const { api } = setup(() => respond(capture("02-meetings-not-logged-in.json")), { onUnauthorized });
    await expect(api.meetings.list()).rejects.toMatchObject({ status: 401, code: "not_authenticated" });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("does not treat a wrong password as a lost session", async () => {
    const onUnauthorized = vi.fn();
    const { api } = setup(
      (url, init) => (init.method === "GET" ? anonymous() : respond(capture("05-login-wrong-password.json"))),
      { onUnauthorized },
    );
    await expect(api.login("owner@example.test", "nope")).rejects.toMatchObject({ code: "invalid_credentials" });
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("refreshes a stale CSRF token once and retries a write that got csrf_rejected", async () => {
    let writes = 0;
    const { api, calls } = setup((url, init) => {
      if (init.method === "GET") return respond(capture("09-session-logged-in.json"));
      writes += 1;
      return writes === 1 ? respond(capture("03-login-without-csrf.json")) : respond(capture("22-template-create.json"));
    });
    const template = await api.templates.create({ name: "Standup", instructions: "One line per person." });
    expect(template).toEqual({ id: 5, name: "Standup", instructions: "One line per person." });
    expect(writes).toBe(2);
    expect(calls.filter((c) => c.init.method === "GET")).toHaveLength(2);
  });

  it("does not retry a rejected write when the session turned out to be logged out", async () => {
    let writes = 0;
    const { api } = setup((url, init) => {
      if (init.method === "GET") return anonymous();
      writes += 1;
      return respond(capture("03-login-without-csrf.json"));
    });
    await expect(api.templates.create({ name: "a", instructions: "b" })).rejects.toMatchObject({ code: "csrf_rejected" });
    expect(writes).toBe(1);
  });

  it("retries a logged-out write once when the token really changed (a login or logout in another tab)", async () => {
    let writes = 0;
    let reads = 0;
    const { api, calls } = setup((url, init) => {
      if (init.method === "GET") {
        reads += 1;
        const body = JSON.stringify({ authenticated: false, csrf_token: reads === 1 ? "token-1" : "token-2", user: null, usage: null, unread_notifications: null });
        return new Response(body, { status: 200, headers: { "content-type": "application/json" } });
      }
      writes += 1;
      return writes === 1 ? respond(capture("03-login-without-csrf.json")) : respond(capture("22-template-create.json"));
    });
    await api.templates.create({ name: "Standup", instructions: "One line per person." });
    expect(writes).toBe(2);
    const sent = calls.filter((c) => c.init.method !== "GET").map((c) => header(c.init, "X-CSRF-Token"));
    expect(sent).toEqual(["token-1", "token-2"]);
  });

  it("returns nothing for a 204", async () => {
    const { api } = setup((url, init) => (init.method === "GET" ? respond(capture("09-session-logged-in.json")) : respond(capture("20-vocabulary-delete.json"))));
    await expect(api.vocabulary.remove(1)).resolves.toBeUndefined();
  });

  it("does not call .json() on a gateway error page", async () => {
    const { api } = setup(() => new Response("<html>504 Gateway Time-out</html>", { status: 504, headers: { "content-type": "text/html" } }));
    const error = (await api.meetings.list().catch((e: unknown) => e)) as ApiError;
    expect(error).toMatchObject({ status: 504, code: "gateway_error", message: GATEWAY_MESSAGE });
  });

  it("reports a request that never got an answer", async () => {
    const { api } = setup(() => {
      throw new TypeError("fetch failed");
    });
    await expect(api.meetings.list()).rejects.toMatchObject({ status: 0, code: "network_error", message: NETWORK_MESSAGE });
  });

  it("passes the extra headers (the visitor's cookie on the server) on every call", async () => {
    const { api, calls } = setup(() => respond(capture("19-vocabulary-list.json")), {
      baseUrl: "http://web:8000/",
      headers: () => ({ cookie: "session=abc" }),
    });
    expect((await api.vocabulary.list()).map((t) => t.term)).toEqual(["Acme Example", "Beyric"]);
    expect(calls[0].url).toBe("http://web:8000/api/vocabulary");
    expect(header(calls[0].init, "cookie")).toBe("session=abc");
  });
});

describe("calendar calls", () => {
  const sessionBody = () => respond(capture("01-session-anonymous.json"));

  it("reads the calendar state with a plain GET", async () => {
    const { api, calls } = setup(() => new Response(JSON.stringify({ state: "not_connected" }), { status: 200, headers: { "content-type": "application/json" } }));
    await api.calendar.get();
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/calendar");
    expect(calls[0].init.method).toBe("GET");
    expect(header(calls[0].init, "X-CSRF-Token")).toBeUndefined();
  });

  it("reads the Recurring cards with a plain GET and the browser's zone in the query", async () => {
    const body = JSON.stringify({ timezone: "Africa/Lagos", series: [] });
    const { api, calls } = setup(() => new Response(body, { status: 200, headers: { "content-type": "application/json" } }));
    await api.calendar.recurring("Africa/Lagos");
    await api.calendar.recurring("America/Argentina/Buenos_Aires");
    await api.calendar.recurring();
    expect(calls.map((c) => c.url)).toEqual([
      "/api/calendar/recurring?tz=Africa%2FLagos",
      "/api/calendar/recurring?tz=America%2FArgentina%2FBuenos_Aires",
      "/api/calendar/recurring",
    ]);
    expect(calls[0].init.method).toBe("GET");
    expect(header(calls[0].init, "X-CSRF-Token")).toBeUndefined();
  });

  it("asks for a translation with the token and a language, and reads, deletes and exports it", async () => {
    const { api, calls } = setup((url, init) =>
      url === "/api/session"
        ? sessionBody()
        : init.method === "DELETE"
          ? new Response(null, { status: 204 })
          : new Response(JSON.stringify({ language: "fr", status: "translating" }), {
              status: 202,
              headers: { "content-type": "application/json" },
            }),
    );
    await api.meetings.translations.request(7, "fr");
    await api.meetings.translations.get(7, "fr");
    await api.meetings.translations.remove(7, "es");
    const post = calls.find((c) => c.url === "/api/meetings/7/translations" && c.init.method === "POST")!;
    expect(header(post.init, "X-CSRF-Token")).toBe("<csrf-A>");
    expect(JSON.parse(post.init.body as string)).toEqual({ language: "fr" });
    const get = calls.find((c) => c.url === "/api/meetings/7/translations/fr")!;
    expect(get.init.method).toBe("GET");
    expect(header(get.init, "X-CSRF-Token")).toBeUndefined();
    const del = calls.find((c) => c.url === "/api/meetings/7/translations/es")!;
    expect(del.init.method).toBe("DELETE");
    expect(header(del.init, "X-CSRF-Token")).toBe("<csrf-A>");
  });

  it("reads and writes the default language with PUT, and asks for an export in a language", async () => {
    const { api, calls } = setup((url) =>
      url === "/api/session"
        ? sessionBody()
        : url.startsWith("/api/settings/language")
          ? new Response(JSON.stringify({ default_language: "fr" }), { status: 200, headers: { "content-type": "application/json" } })
          : new Response("x", { status: 200, headers: { "content-type": "application/pdf" } }),
    );
    expect(await api.settings.language.get()).toEqual({ default_language: "fr" });
    await api.settings.language.set("es");
    await api.meetings.export(7, "pdf", true, "fr");
    const put = calls.find((c) => c.init.method === "PUT")!;
    expect(put.url).toBe("/api/settings/language");
    expect(JSON.parse(put.init.body as string)).toEqual({ default_language: "es" });
    expect(header(put.init, "X-CSRF-Token")).toBe("<csrf-A>");
    expect(calls.at(-1)!.url).toBe("/api/meetings/7/export?format=pdf&transcript=true&language=fr");
  });

  it("starts the connect with the CSRF token and a return to the settings page", async () => {
    const { api, calls } = setup((url) =>
      url === "/api/session"
        ? sessionBody()
        : new Response(JSON.stringify({ authorize_url: "https://accounts.example.test/x", show_unverified_notice: false }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
    );
    const out = await api.calendar.connect();
    expect(out.authorize_url).toBe("https://accounts.example.test/x");
    const post = calls.find((c) => c.url === "/api/calendar/google/connect")!;
    expect(post.init.method).toBe("POST");
    expect(header(post.init, "X-CSRF-Token")).toBe("<csrf-A>");
    expect(JSON.parse(post.init.body as string)).toEqual({ features: ["calendar"], return_to: "/settings" });
  });

  it("disconnects with the CSRF token and no body", async () => {
    const { api, calls } = setup((url) =>
      url === "/api/session"
        ? sessionBody()
        : new Response(JSON.stringify({ status: "disconnected", revoked: true }), { status: 200, headers: { "content-type": "application/json" } }),
    );
    expect(await api.calendar.disconnect()).toEqual({ status: "disconnected", revoked: true });
    const post = calls.find((c) => c.url === "/api/calendar/google/disconnect")!;
    expect(post.init.method).toBe("POST");
    expect(post.init.body).toBeUndefined();
    expect(header(post.init, "X-CSRF-Token")).toBe("<csrf-A>");
  });

  it("writes the rule and the template to /api/calendar/settings with the CSRF token", async () => {
    const { api, calls } = setup((url) =>
      url === "/api/session"
        ? sessionBody()
        : new Response(JSON.stringify({ rule: "organizer" }), { status: 200, headers: { "content-type": "application/json" } }),
    );
    expect(await api.calendar.settings({ rule: "organizer" })).toEqual({ rule: "organizer" });
    await api.calendar.settings({ calendar_template_id: null });
    const puts = calls.filter((c) => c.url === "/api/calendar/settings");
    expect(puts).toHaveLength(2);
    expect(puts[0].init.method).toBe("PUT");
    expect(header(puts[0].init, "X-CSRF-Token")).toBe("<csrf-A>");
    expect(JSON.parse(puts[0].init.body as string)).toEqual({ rule: "organizer" });
    expect(JSON.parse(puts[1].init.body as string)).toEqual({ calendar_template_id: null });
  });

  it("switches one meeting with PUT /api/calendar/events/{id}/override, null clearing the switch", async () => {
    const { api, calls } = setup((url) =>
      url === "/api/session"
        ? sessionBody()
        : new Response(JSON.stringify({ id: 7 }), { status: 200, headers: { "content-type": "application/json" } }),
    );
    await api.calendar.override(7, true);
    await api.calendar.override(7, null);
    const puts = calls.filter((c) => c.url === "/api/calendar/events/7/override");
    expect(puts).toHaveLength(2);
    expect(puts[0].init.method).toBe("PUT");
    expect(header(puts[0].init, "X-CSRF-Token")).toBe("<csrf-A>");
    expect(JSON.parse(puts[0].init.body as string)).toEqual({ override: true });
    expect(JSON.parse(puts[1].init.body as string)).toEqual({ override: null });
  });

  it("throws the 409 not_joinable of the override route with its words", async () => {
    const { api } = setup((url) =>
      url === "/api/session"
        ? sessionBody()
        : new Response(JSON.stringify({ error: { code: "not_joinable", message: "You declined this invite" } }), {
            status: 409,
            headers: { "content-type": "application/json" },
          }),
    );
    await expect(api.calendar.override(5, true)).rejects.toMatchObject({
      status: 409,
      code: "not_joinable",
      message: "You declined this invite",
    });
  });

  it("throws the server's error for the calendar being off", async () => {
    const { api } = setup(() =>
      new Response(JSON.stringify({ error: { code: "calendar_unavailable", message: "Google Calendar is not available." } }), {
        status: 404,
        headers: { "content-type": "application/json" },
      }),
    );
    await expect(api.calendar.get()).rejects.toMatchObject({ status: 404, code: "calendar_unavailable" });
  });
});
