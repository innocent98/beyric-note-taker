"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Alert, Button, Field } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";

// POST /api/session from the browser, so the session cookie FastAPI sets lands in the browser by
// itself. The button stays locked after a 429: the lockout lasts 5 minutes to an hour with no
// countdown, so there is nothing to retry for now (docs/fe-integration-guide-owner-app.md section 8).
// The sign-in is one password: the API has no email, "forgot password" or "keep me signed in"
// (docs/ui/parity-report.md), so the form does not draw them.
export function LoginForm() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [locked, setLocked] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || locked) return;
    if (password === "") {
      setError("Enter your password.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      await api.login(password);
      router.replace("/");
      router.refresh();
    } catch (caught) {
      setError(errorMessage(caught));
      if (caught instanceof ApiError && caught.code === "too_many_attempts") setLocked(true);
      setPending(false);
    }
  }

  return (
    <form className="auth-form" onSubmit={submit} noValidate>
      <div>
        <h1 className="auth-title">Welcome back</h1>
        <p className="auth-sub">Sign in to see your meeting notes and tasks.</p>
      </div>
      {error ? <Alert kind="error">{error}</Alert> : null}
      <Field
        id="password"
        label="Password"
        type={show ? "text" : "password"}
        large
        autoFocus
        autoComplete="current-password"
        placeholder="Your password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        end={
          <Button kind="quiet" type="button" onClick={() => setShow((value) => !value)} aria-pressed={show}>
            {show ? "Hide" : "Show"}
          </Button>
        }
      />
      <Button size="field" kind="primary" block disabled={pending || locked}>
        {pending ? "Signing in..." : "Sign in"}
      </Button>
    </form>
  );
}
