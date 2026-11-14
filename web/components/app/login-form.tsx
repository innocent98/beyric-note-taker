"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { GoogleButton } from "@/components/auth/google-button";
import { Alert, AppLink, Button, Field } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { validEmail } from "@/lib/accounts";
import { ApiError, errorMessage } from "@/lib/api/client";
import { hasPendingShare } from "@/lib/share-pending";
import { takeSetupPending } from "@/lib/setup-pending";

// POST /api/session from the browser, so the session cookie FastAPI sets lands in the browser by
// itself. The button stays locked after a 429: the lockout lasts 5 minutes to an hour with no
// countdown, so there is nothing to retry for now (docs/fe-integration-guide-owner-app.md section 8).
// The sign-in is an email and a password, with "Forgot password?" and, when GET /api/pilot says the
// server has a Google client (`google`), "Continue with Google". `redirectError` is the message for
// /login?error=<code> (the Google callback's redirects); `redirectSignup` adds a link to the sign-up
// page for the codes that need it. There is no "keep me signed in" (the session lifetime is not
// optional) and no Microsoft button (docs/ui/parity-report.md). A wrong password for an unconfirmed
// account is the same 401 as any other (guide section 5), so the error carries a line about it.
export function LoginForm({
  google = false,
  redirectError = null,
  redirectSignup = false,
}: {
  google?: boolean;
  redirectError?: string | null;
  redirectSignup?: boolean;
}) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(redirectError);
  // The designer's field-level lines under the inputs (Auth.dc.html:370-371); the API's own answer
  // (wrong password, lockout) stays in the alert above the form.
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({});
  const [wrongCredentials, setWrongCredentials] = useState(false);
  const [pending, setPending] = useState(false);
  const [locked, setLocked] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || locked) return;
    const found: { email?: string; password?: string } = {};
    if (email.trim() === "") found.email = "Enter your email.";
    else if (!validEmail(email)) found.email = "Enter a valid email, like you@company.com.";
    if (password === "") found.password = "Enter your password.";
    setFieldErrors(found);
    if (found.email || found.password) return;
    setPending(true);
    setError(null);
    setWrongCredentials(false);
    try {
      await api.login(email.trim(), password);
      // A meeting link shared from another app (the PWA share target) is waiting in this tab: back to it.
      // A new account's first sign-in (the confirmation page marked this tab) goes to the workspace step.
      const setup = takeSetupPending();
      router.replace(hasPendingShare() ? "/share" : setup ? "/setup" : "/meetings");
      router.refresh();
    } catch (caught) {
      setError(errorMessage(caught));
      if (caught instanceof ApiError && caught.code === "invalid_credentials") setWrongCredentials(true);
      if (caught instanceof ApiError && caught.code === "too_many_attempts") setLocked(true);
      setPending(false);
    }
  }

  return (
    <form className="auth-form" method="post" onSubmit={submit} noValidate>
      <div>
        <h1 className="auth-title">Welcome back</h1>
        <p className="auth-sub">Sign in to see your meeting notes and tasks.</p>
      </div>
      {google ? (
        <div className="auth-sso">
          <GoogleButton />
          <div className="auth-or">or sign in with email</div>
        </div>
      ) : null}
      {error ? (
        <Alert kind="error">
          {error}
          {wrongCredentials ? (
            <>
              <br />
              If you have just signed up, confirm your email first: check your inbox for the link.
            </>
          ) : null}
          {error === redirectError && redirectSignup ? (
            <>
              {" "}
              <AppLink className="auth-alert-link" href="/signup">
                Go to sign-up
              </AppLink>
            </>
          ) : null}
        </Alert>
      ) : null}
      <div className="auth-fields">
        <Field
          id="email"
          label="Work email"
          type="email"
          large
          autoFocus
          autoComplete="email"
          placeholder="you@company.com"
          value={email}
          error={fieldErrors.email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <Field
          id="password"
          label="Password"
          labelEnd={<AppLink href="/forgot-password">Forgot password?</AppLink>}
          type={show ? "text" : "password"}
          large
          autoComplete="current-password"
          placeholder="Your password"
          value={password}
          error={fieldErrors.password}
          onChange={(event) => setPassword(event.target.value)}
          end={
            <Button kind="quiet" type="button" onClick={() => setShow((value) => !value)} aria-pressed={show}>
              {show ? "Hide" : "Show"}
            </Button>
          }
        />
      </div>
      <Button size="field" kind="primary" block disabled={pending || locked}>
        {pending ? "Signing in..." : "Sign in"}
      </Button>
    </form>
  );
}
