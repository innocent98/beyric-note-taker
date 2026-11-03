"use client";

import { useState, type FormEvent } from "react";
import { Alert, Button, Field } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { RESEND_WAIT_SECONDS, accountError, fieldOf, validEmail } from "@/lib/accounts";
import { AuthHead } from "./auth-head";
import { MailLinks } from "./mail-links";
import { useCountdown } from "./use-countdown";

// /forgot-password and its "check your email" state. POST /api/password/forgot answers 202 for a
// known and an unknown address alike, so the sent screen says "if an account exists" and nothing
// more (guide section 7). The link expires in 30 minutes and signs the user out everywhere when used.
export function ForgotPasswordFlow() {
  const [email, setEmail] = useState("");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState<string | undefined>();
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const wait = useCountdown(0);

  async function send(address: string): Promise<boolean> {
    try {
      await api.forgotPassword(address);
      return true;
    } catch (caught) {
      if (fieldOf(caught) === "email") setError(accountError(caught));
      else setFormError(accountError(caught));
      return false;
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const address = email.trim();
    setFormError(null);
    if (!validEmail(address)) return setError(address ? "Enter a valid email, like you@company.com." : "Enter your email.");
    setError(undefined);
    setPending(true);
    if (await send(address)) {
      setSentTo(address);
      setNote(null);
      wait.start(RESEND_WAIT_SECONDS);
    }
    setPending(false);
  }

  async function resend() {
    if (pending || wait.left > 0 || !sentTo) return;
    setPending(true);
    setNote(null);
    if (await send(sentTo)) {
      setNote("Sent again, if that address has an account.");
      wait.start(RESEND_WAIT_SECONDS);
    } else setNote("That did not go through. Try again in a moment.");
    setPending(false);
  }

  if (sentTo) {
    return (
      <div className="auth-form" data-screen="reset-link-sent">
        <AuthHead tile="mail" title="Check your email">
          If an account exists for <span className="auth-who">{sentTo}</span>, a reset link is on its way. It expires in
          30 minutes.
        </AuthHead>
        <MailLinks />
        <div className="auth-stack-sm">
          <div className="auth-small">
            Did not get it? Check spam, or
            {wait.left > 0 ? (
              <span className="wait">resend in {wait.label}</span>
            ) : (
              <Button kind="link" type="button" onClick={resend} disabled={pending}>
                resend the link
              </Button>
            )}
          </div>
          {note ? (
            <p className="auth-small" role="status">
              {note}
            </p>
          ) : null}
          <div className="auth-small">
            Wrong email?
            <Button kind="link" type="button" onClick={() => setSentTo(null)}>
              Change it
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <form className="auth-form" method="post" onSubmit={submit} noValidate>
      <AuthHead tile="lock" title="Reset your password">
        Enter the email you signed up with and we will send you a reset link.
      </AuthHead>
      {formError ? <Alert kind="error">{formError}</Alert> : null}
      <Field
        id="email"
        label="Work email"
        type="email"
        large
        autoFocus
        autoComplete="email"
        placeholder="you@company.com"
        value={email}
        error={error}
        onChange={(event) => setEmail(event.target.value)}
      />
      <Button size="field" block disabled={pending}>
        {pending ? "Sending..." : "Send reset link"}
      </Button>
    </form>
  );
}
