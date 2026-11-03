"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Alert, Button, Field } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";

// POST /api/session from the browser, so the session cookie FastAPI sets lands in the browser by
// itself. The button stays locked after a 429: the lockout is 15 minutes with no countdown, so
// there is nothing to retry for now (docs/fe-integration-guide-owner-app.md section 8).
export function LoginForm() {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [locked, setLocked] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || locked) return;
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
    <form className="stack-sm" onSubmit={submit}>
      {error ? <Alert kind="error">{error}</Alert> : null}
      <Field
        id="password"
        label="Password"
        type="password"
        required
        autoFocus
        autoComplete="current-password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
      />
      <Button block disabled={pending || locked}>
        {pending ? "Signing in..." : "Sign in"}
      </Button>
    </form>
  );
}
