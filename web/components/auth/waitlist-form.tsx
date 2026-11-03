"use client";

import { useState, type FormEvent } from "react";
import { Alert, Button, Field } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { accountError, fieldOf, validEmail } from "@/lib/accounts";

// POST /api/waitlist: always 202 `on_the_list`, whether or not the address is already listed or has
// an account (docs/fe-integration-guide-pilot-accounts.md section 8), so the done state says nothing
// about which. `initialEmail` is the pilot's `prefill_email` (a Google visitor just turned away).
export function WaitlistForm({ initialEmail = "" }: { initialEmail?: string }) {
  const [email, setEmail] = useState(initialEmail);
  const [name, setName] = useState("");
  const [emailError, setEmailError] = useState<string | undefined>();
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const address = email.trim();
    setFormError(null);
    if (!address) return setEmailError("Enter your email.");
    if (!validEmail(address)) return setEmailError("Enter a valid email, like you@company.com.");
    setEmailError(undefined);
    setPending(true);
    try {
      await api.joinWaitlist(address, name.trim() || undefined);
      setDone(address);
    } catch (caught) {
      if (fieldOf(caught) === "email") setEmailError(accountError(caught));
      else setFormError(accountError(caught));
    } finally {
      setPending(false);
    }
  }

  if (done) {
    return (
      <Alert kind="info" role="status">
        <strong>You are on the list.</strong> We will write to <span className="auth-who">{done}</span> when a place opens.
      </Alert>
    );
  }

  return (
    <form className="auth-form" method="post" onSubmit={submit} noValidate aria-label="Join the waiting list">
      {formError ? <Alert kind="error">{formError}</Alert> : null}
      <Field
        id="waitlist-email"
        label="Email"
        type="email"
        large
        autoComplete="email"
        placeholder="you@company.com"
        value={email}
        error={emailError}
        onChange={(event) => setEmail(event.target.value)}
      />
      <Field
        id="waitlist-name"
        label="Name (optional)"
        large
        autoComplete="name"
        placeholder="Ada Okafor"
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <Button size="field" block disabled={pending}>
        {pending ? "Joining..." : "Join the waiting list"}
      </Button>
    </form>
  );
}
