import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ForgotPasswordPage from "@/app/forgot-password/page";
import LoginPage from "@/app/login/page";
import PilotFullPage from "@/app/pilot-full/page";
import ResetPasswordPage from "@/app/reset-password/page";
import SignupPage from "@/app/signup/page";
import VerifyEmailPage from "@/app/verify-email/page";
import type { Pilot } from "@/lib/api/types";
import { accountBody } from "./helpers";

// The server half of each page: it reads GET /api/pilot and the query string and hands the client
// component what it needs. The pilot bodies are the captured ones.

const state = vi.hoisted(() => ({ authenticated: false, pilot: null as unknown }));
vi.mock("@/lib/api/server", () => ({
  getSession: async () => ({ authenticated: state.authenticated }),
  getPilot: async () => state.pilot,
}));
vi.mock("next/navigation", () => ({
  redirect: (to: string) => {
    throw new Error(`redirect:${to}`);
  },
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }),
}));

const open = accountBody<Pilot>("11-pilot-open.json");
const closed = accountBody<Pilot>("04-pilot-closed.json");
const full = accountBody<Pilot>("57-pilot-full.json");
const prefilled = accountBody<Pilot>("122-google-pilot-prefill.json");

const html = async (page: React.ReactElement | Promise<React.ReactElement>) => renderToStaticMarkup(await page);
const query = <T extends object>(q: T) => ({ searchParams: Promise.resolve(q) });

beforeEach(() => {
  state.authenticated = false;
  state.pilot = open;
});

describe("/signup", () => {
  it("draws the closed screen, not a form, while the pilot is closed (the real pilot's state)", async () => {
    state.pilot = closed;
    const out = await html(SignupPage(query({})));
    expect(out).toContain("Sign-up is not open yet");
    expect(out).toContain("Join the waiting list");
    expect(out).not.toContain("Create your account");
  });

  it("draws the pilot-full screen while the pilot is full", async () => {
    state.pilot = full;
    expect(await html(SignupPage(query({})))).toContain("The pilot is full");
  });

  it("draws the form, with Google, while the pilot is open", async () => {
    const out = await html(SignupPage(query({})));
    expect(out).toContain("Create your account");
    expect(out).toContain("Continue with Google");
    expect(out).toContain('method="post"');
  });

  it("leaves Google out when the pilot says google_signin is false", async () => {
    // 02 is the closed pilot with Google off; here the pilot is open, so the form is what is drawn.
    state.pilot = { ...open, google_signin: false };
    expect(await html(SignupPage(query({})))).not.toContain("Continue with Google");
  });

  it("draws the form for an invitation even while the pilot is closed", async () => {
    state.pilot = closed;
    const out = await html(SignupPage(query({ invite: "invite-token-placeholder" })));
    expect(out).toContain("Create your account");
    expect(out).toContain("You have an invitation to the pilot");
    // The server only learns that there is an invitation; the token never reaches the page.
    expect(out).not.toContain("invite-token-placeholder");
  });

  it("sends a signed-in visitor to the app", async () => {
    state.authenticated = true;
    await expect(SignupPage(query({}))).rejects.toThrow("redirect:/");
  });
});

describe("/login", () => {
  it("shows the Google button only when the pilot says so", async () => {
    expect(await html(LoginPage(query({})))).toContain("Continue with Google");
    state.pilot = { ...open, google_signin: false };
    expect(await html(LoginPage(query({})))).not.toContain("Continue with Google");
    state.pilot = null;
    expect(await html(LoginPage(query({})))).not.toContain("Continue with Google");
  });

  it.each([
    ["google_denied", "Google sign-in was cancelled."],
    ["google_failed", "We could not sign you in with Google."],
    ["terms_required", "accept the Terms and the Privacy Policy first"],
    ["pilot_closed", "Sign-up is not open yet."],
    ["account_suspended", "This account is suspended."],
    ["email_unsupported", "We cannot accept that Google address yet"],
  ])("shows the message for ?error=%s", async (code, words) => {
    const out = await html(LoginPage(query({ error: code })));
    expect(out).toContain(words);
  });

  it("links to sign-up for the two codes that need it, and shows only a general line for an unknown code", async () => {
    expect(await html(LoginPage(query({ error: "terms_required" })))).toContain("Go to sign-up");
    expect(await html(LoginPage(query({ error: "google_denied" })))).not.toContain("Go to sign-up");
    const unknown = await html(LoginPage(query({ error: "<b>x</b>" })));
    expect(unknown).toContain("Sign-in did not work. Please try again.");
    expect(unknown).not.toContain("<b>x</b>");
  });

  it("has the Create account link in the header", async () => {
    expect(await html(LoginPage(query({})))).toContain('href="/signup"');
  });
});

describe("/pilot-full", () => {
  it("fills the waiting list form from prefill_email", async () => {
    state.pilot = prefilled;
    const out = await html(PilotFullPage());
    expect(out).toContain("The pilot is full");
    expect(out).toContain('value="latecomer@example.test"');
  });

  it("draws the closed screen when the pilot has closed since, and a link to sign up when it has opened", async () => {
    state.pilot = closed;
    expect(await html(PilotFullPage())).toContain("Sign-up is not open yet");
    state.pilot = open;
    expect(await html(PilotFullPage())).toContain("A place has opened since you tried");
  });
});

describe("the pages the mails open", () => {
  it("/verify-email draws a form with the password field, and the page takes no token from the server", async () => {
    const out = await html(VerifyEmailPage());
    expect(out).toContain("Confirm your email");
    expect(out).toContain("Your password");
    expect(VerifyEmailPage.length).toBe(0);
  });

  it("/reset-password draws the new-password form, and the page takes no token from the server", async () => {
    const out = await html(ResetPasswordPage());
    expect(out).toContain("Choose a new password");
    expect(out).toContain("Confirm password");
    expect(ResetPasswordPage.length).toBe(0);
  });

  it("/forgot-password draws the form, and sends a signed-in visitor to the app", async () => {
    expect(await html(ForgotPasswordPage())).toContain("Reset your password");
    state.authenticated = true;
    await expect(ForgotPasswordPage()).rejects.toThrow("redirect:/");
  });
});
