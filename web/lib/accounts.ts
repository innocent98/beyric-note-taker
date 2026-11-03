import { ApiError, errorMessage } from "@/lib/api/client";

// Rules and wording for the account screens (docs/fe-integration-guide-pilot-accounts.md). The
// passwords are checked by the server alone beyond their length: 8 to 128 characters and not on a
// list of common ones, so the screens say no more than that.
export const PASSWORD_MIN = 8;
export const PASSWORD_MAX = 128;

// The length message for a password, or undefined when the length is fine.
export function lengthProblem(value: string): string | undefined {
  if (value.length < PASSWORD_MIN) return `Use at least ${PASSWORD_MIN} characters.`;
  if (value.length > PASSWORD_MAX) return `Use at most ${PASSWORD_MAX} characters.`;
  return undefined;
}

export function validEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
}

// How long the "resend" button waits: the server drops a second mail for the same address within a
// minute without saying so (section 6), so a shorter wait would promise something it cannot keep.
export const RESEND_WAIT_SECONDS = 60;

// Codes whose `message` the API writes for the visitor, including the 422s that `errorMessage`
// (the owner app's rule: a 422 is developer text) would hide. Every other code falls back to it.
const SHOWABLE = new Set([
  "pilot_closed",
  "pilot_full",
  "password_signup_disabled",
  "invite_invalid",
  "password_too_weak",
  "terms_required",
  "invalid_email",
  "invalid_name",
  "validation_error",
  "password_mismatch",
  "token_invalid_or_expired",
  "too_many_requests",
  "server_busy",
]);

export function accountError(error: unknown): string {
  if (error instanceof ApiError && SHOWABLE.has(error.code)) return error.message;
  return errorMessage(error);
}

export function isCode(error: unknown, ...codes: string[]): boolean {
  return error instanceof ApiError && codes.includes(error.code);
}

// Which field a sign-up or reset error belongs under; anything else is shown above the form.
export function fieldOf(error: unknown): "password" | "email" | "name" | "terms" | null {
  if (!(error instanceof ApiError)) return null;
  switch (error.code) {
    case "password_too_weak":
      return "password";
    case "invalid_email":
      return "email";
    case "invalid_name":
      return "name";
    case "terms_required":
      return "terms";
    default:
      return null;
  }
}

// What /login?error=<code> says. The codes are the ones the Google callback redirects with
// (section 9); the words are ours. An unknown code gets the general line, never the raw code.
export const LOGIN_ERRORS = {
  google_failed: "We could not sign you in with Google. Please try again.",
  google_denied: "Google sign-in was cancelled. You can try again, or sign in with your email and password.",
  terms_required: "To create an account with Google, accept the Terms and the Privacy Policy first. Start on the sign-up page.",
  pilot_closed: "Sign-up is not open yet. Check back soon or join the waiting list.",
  account_suspended: "This account is suspended.",
  email_unsupported: "We cannot accept that Google address yet because it has letters outside a to z. Use another Google account or your email and password.",
} as const;

export const LOGIN_ERROR_FALLBACK = "Sign-in did not work. Please try again.";

export function loginErrorMessage(code: string | undefined): string | null {
  if (!code) return null;
  return Object.hasOwn(LOGIN_ERRORS, code) ? LOGIN_ERRORS[code as keyof typeof LOGIN_ERRORS] : LOGIN_ERROR_FALLBACK;
}

// Codes after which a visitor needs somewhere to go (the sign-up page, where the waitlist is).
export const LOGIN_ERRORS_WITH_SIGNUP: ReadonlySet<string> = new Set(["terms_required", "pilot_closed"]);

// A full page load to Google's consent screen through our own route (section 9). `accept_terms`
// goes on only when the visitor ticked the box.
export function googleStartUrl(acceptTerms: boolean): string {
  const query = new URLSearchParams({ return_to: "/" });
  if (acceptTerms) query.set("accept_terms", "true");
  return `/api/auth/google/start?${query.toString()}`;
}
