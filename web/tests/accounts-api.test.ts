import { describe, expect, it, vi } from "vitest";
import { ApiError, createApi, RATE_LIMIT_MESSAGE } from "@/lib/api/client";
import { accountError, googleStartUrl, loginErrorMessage, LOGIN_ERRORS, fieldOf } from "@/lib/accounts";
import { accountBody, capturedAccount } from "./helpers";

// The accounts calls, tested against the captured responses of the running app, replayed byte for byte.

type Call = { url: string; init: RequestInit };

function setup(answer: (call: Call) => Response) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const call = { url: String(input), init };
    calls.push(call);
    if (call.url === "/api/session") {
      return new Response(JSON.stringify(accountBody("01-session-anonymous.json")), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
    return answer(call);
  });
  return { api: createApi({ fetch: fetchMock as unknown as typeof fetch }), calls };
}

function reply(file: string): Response {
  const { status, body } = capturedAccount(file).response;
  return new Response(body === "" ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

const header = (init: RequestInit, name: string) => (init.headers as Record<string, string>)[name];

describe("accounts calls", () => {
  it("reads the pilot with a GET and no token", async () => {
    const { api, calls } = setup(() => reply("11-pilot-open.json"));
    expect(await api.pilot()).toEqual({ state: "open", prefill_email: null, google_signin: true });
    expect(calls.map((c) => `${c.init.method} ${c.url}`)).toEqual(["GET /api/pilot"]);
    expect(header(calls[0].init, "X-CSRF-Token")).toBeUndefined();
  });

  it.each([
    ["04-pilot-closed.json", "closed"],
    ["57-pilot-full.json", "full"],
  ])("reads %s", async (file, state) => {
    const { api } = setup(() => reply(file));
    expect(await api.pilot()).toMatchObject({ state });
  });

  it("keeps prefill_email from the full pilot", async () => {
    const { api } = setup(() => reply("122-google-pilot-prefill.json"));
    expect(await api.pilot()).toEqual({ state: "full", prefill_email: "latecomer@example.test", google_signin: true });
  });

  it("sends the CSRF token with every public write, and the bodies the guide shows", async () => {
    const { api, calls } = setup((call) => {
      if (call.url === "/api/signup") return reply("18-signup-ok.json");
      if (call.url === "/api/signup/resend") return reply("35-resend-unverified.json");
      if (call.url === "/api/verify-email") return reply("24-verify-ok.json");
      if (call.url === "/api/password/forgot") return reply("39-forgot-known.json");
      if (call.url === "/api/password/reset") return reply("44-reset-ok.json");
      return reply("49-waitlist-join.json");
    });
    expect(await api.signup({ name: "Ada Example", email: "ada@example.test", password: "pw-placeholder", accept_terms: true })).toEqual({
      status: "check_your_email",
    });
    expect(await api.resendVerification("ben@example.test")).toEqual({ status: "check_your_email" });
    expect(await api.verifyEmail("tok", "pw-placeholder")).toEqual({ status: "verified" });
    expect(await api.forgotPassword("ada@example.test")).toEqual({ status: "check_your_email" });
    expect(await api.resetPassword("tok", "pw-placeholder")).toEqual({ status: "password_changed" });
    expect(await api.joinWaitlist("wendy@example.test", "Wendy Example")).toEqual({ status: "on_the_list" });
    const writes = calls.filter((c) => c.init.method === "POST");
    expect(writes.map((c) => c.url)).toEqual([
      "/api/signup",
      "/api/signup/resend",
      "/api/verify-email",
      "/api/password/forgot",
      "/api/password/reset",
      "/api/waitlist",
    ]);
    for (const write of writes) expect(header(write.init, "X-CSRF-Token")).toBe("<csrf-A>");
    expect(writes.map((c) => JSON.parse(String(c.init.body)))).toEqual([
      { name: "Ada Example", email: "ada@example.test", password: "pw-placeholder", accept_terms: true },
      { email: "ben@example.test" },
      { token: "tok", password: "pw-placeholder" },
      { email: "ada@example.test" },
      { token: "tok", password: "pw-placeholder" },
      { email: "wendy@example.test", name: "Wendy Example" },
    ]);
    // GET /api/session was read once, for the first write.
    expect(calls.filter((c) => c.url === "/api/session")).toHaveLength(1);
  });

  it("sends the waiting list a body without a name when there is none", async () => {
    const { api, calls } = setup(() => reply("49-waitlist-join.json"));
    await api.joinWaitlist("wendy@example.test");
    expect(JSON.parse(String(calls.at(-1)!.init.body))).toEqual({ email: "wendy@example.test" });
  });

  it.each([
    ["05-signup-while-closed.json", 403, "pilot_closed"],
    ["59-signup-pilot-full.json", 409, "pilot_full"],
    ["12-signup-weak-password.json", 422, "password_too_weak"],
    ["13-signup-terms-not-accepted.json", 422, "terms_required"],
    ["14-signup-bad-email.json", 422, "invalid_email"],
    ["15-signup-missing-field.json", 422, "validation_error"],
    ["17-signup-without-csrf.json", 403, "csrf_rejected"],
    ["82-signup-with-invite-wrong-address.json", 400, "invite_invalid"],
    ["124-signup-rate-limited.json", 429, "too_many_requests"],
  ])("turns %s into an ApiError", async (file, status, code) => {
    const { api } = setup(() => reply(file));
    const error = (await api
      .signup({ name: "Ada Example", email: "ada@example.test", password: "pw-placeholder", accept_terms: true })
      .catch((e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status, code });
  });

  it("turns nginx's own 429 page (not JSON) into the same too-many-attempts error", async () => {
    const { api } = setup(
      () => new Response("<html><body><h1>429 Too Many Requests</h1></body></html>", { status: 429, headers: { "content-type": "text/html" } }),
    );
    const error = (await api.signup({ name: "A", email: "a@b.c", password: "pw-placeholder", accept_terms: true }).catch((e: unknown) => e)) as ApiError;
    expect(error).toMatchObject({ status: 429, code: "too_many_requests", message: RATE_LIMIT_MESSAGE });
    expect(accountError(error)).toBe("Too many attempts. Try again later.");
  });
});

describe("account messages", () => {
  it("shows the API's own message for the 422s the owner app would hide, and the fallback for others", () => {
    const weak = new ApiError(422, "password_too_weak", "That password is too common. Choose one that is harder to guess.");
    expect(accountError(weak)).toBe(weak.message);
    expect(accountError(new ApiError(404, "not_found", "Not Found"))).toBe("Something went wrong. Please try again.");
    expect(accountError(new ApiError(403, "csrf_rejected", "Missing or wrong X-CSRF-Token header."))).toBe(
      "Your session changed. Reload the page and try again.",
    );
    expect(accountError(new Error("boom"))).toBe("Something went wrong. Please try again.");
  });

  it("names the field an error belongs under", () => {
    expect(fieldOf(new ApiError(422, "password_too_weak", "x"))).toBe("password");
    expect(fieldOf(new ApiError(422, "invalid_email", "x"))).toBe("email");
    expect(fieldOf(new ApiError(422, "invalid_name", "x"))).toBe("name");
    expect(fieldOf(new ApiError(422, "terms_required", "x"))).toBe("terms");
    expect(fieldOf(new ApiError(429, "too_many_requests", "x"))).toBeNull();
  });

  it("has a message for every redirect code of the Google callback and none for a made-up one", () => {
    for (const code of ["google_failed", "google_denied", "terms_required", "pilot_closed", "account_suspended", "email_unsupported"]) {
      expect(loginErrorMessage(code), code).toBe(LOGIN_ERRORS[code as keyof typeof LOGIN_ERRORS]);
    }
    expect(loginErrorMessage(undefined)).toBeNull();
    expect(loginErrorMessage("<script>")).toBe("Sign-in did not work. Please try again.");
    expect(loginErrorMessage("constructor")).toBe("Sign-in did not work. Please try again.");
  });

  it("builds the Google start address, with accept_terms only when ticked", () => {
    expect(googleStartUrl(false)).toBe("/api/auth/google/start?return_to=%2F");
    expect(googleStartUrl(true)).toBe("/api/auth/google/start?return_to=%2F&accept_terms=true");
  });
});
