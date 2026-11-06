"use client";

import { useState, type FormEvent } from "react";
import { Alert, Button } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { accountError, isCode } from "@/lib/accounts";
import { AuthHead } from "./auth-head";
import { useUrlToken } from "./use-url-token";

type Screen = "form" | "done" | "dead";

// /unsubscribe?token=...: LEAD-DESIGNED, not in the designer's file (built from verify-email-form).
// The button posts the token from the link (docs/fe-integration-guide-share.md section 2.3); the
// token works once. "Done" says what stops and what does not: mail that lets a person into their own
// account (a sign-in link, a password reset) is not stopped.
export function UnsubscribeForm() {
  const { ready, token } = useUrlToken("token");
  const [screen, setScreen] = useState<Screen>("form");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || !token) return;
    setError(null);
    setPending(true);
    try {
      await api.optOutOfMail(token);
      setScreen("done");
    } catch (caught) {
      if (isCode(caught, "token_invalid_or_expired")) setScreen("dead");
      else setError(accountError(caught));
    } finally {
      setPending(false);
    }
  }

  const missing = ready && !token && screen === "form";

  if (screen === "done") {
    return (
      <div className="auth-form" data-screen="unsubscribed">
        <AuthHead tile="check" tone="success" title="You will not get these emails">
          We will stop sending notes and reminders from Beyric Note Taker to this address. Emails that let you into an
          account you hold, such as a sign-in link or a password reset, still arrive.
        </AuthHead>
      </div>
    );
  }

  if (screen === "dead" || missing) {
    return (
      <div className="auth-form" data-screen="unsubscribe-dead">
        <AuthHead tile="mail" tone="warning" title="This link does not work">
          {missing
            ? "This link is missing or was already used. If the emails still come, use the link at the bottom of the latest one, or write to privacy@beyrictech.com."
            : "This link has expired or was already used. If the emails still come, use the link at the bottom of the latest one, or write to privacy@beyrictech.com."}
        </AuthHead>
      </div>
    );
  }

  return (
    <form className="auth-form" method="post" onSubmit={submit} noValidate>
      <AuthHead tile="mail" title="Stop emails from Beyric Note Taker?">
        Someone shared meeting notes with you from Beyric Note Taker. Confirm to stop notes and reminders from it to this
        address.
      </AuthHead>
      {error ? <Alert kind="error">{error}</Alert> : null}
      <Button size="field" block disabled={pending}>
        {pending ? "Stopping..." : "Stop these emails"}
      </Button>
    </form>
  );
}
