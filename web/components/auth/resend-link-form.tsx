"use client";

import { useState, type FormEvent } from "react";
import { Alert, Button, Field } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { accountError, fieldOf, validEmail } from "@/lib/accounts";

// "Send a new link" for a confirmation link that is dead (POST /api/signup/resend). The answer is
// always 202, so nothing here says whether a mail was sent (section 6).
export function ResendLinkForm() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | undefined>();
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setFormError(null);
    if (!validEmail(email)) return setError(email.trim() ? "Enter a valid email, like you@company.com." : "Enter your email.");
    setError(undefined);
    setPending(true);
    try {
      await api.resendVerification(email.trim());
      setSent(true);
    } catch (caught) {
      if (fieldOf(caught) === "email") setError(accountError(caught));
      else setFormError(accountError(caught));
    } finally {
      setPending(false);
    }
  }

  if (sent) {
    return (
      <Alert kind="info" role="status">
        If that address is waiting to be confirmed, a new link is on its way. It can take a few minutes, and it asks for
        the same password as before.
      </Alert>
    );
  }
  return (
    <form className="auth-form" method="post" onSubmit={submit} noValidate aria-label="Send a new link">
      {formError ? <Alert kind="error">{formError}</Alert> : null}
      <Field
        id="resend-email"
        label="Email you signed up with"
        type="email"
        large
        autoComplete="email"
        placeholder="you@company.com"
        value={email}
        error={error}
        onChange={(event) => setEmail(event.target.value)}
      />
      <Button size="field" block disabled={pending}>
        {pending ? "Sending..." : "Send a new link"}
      </Button>
    </form>
  );
}
