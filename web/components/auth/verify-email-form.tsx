"use client";

import { useState, type FormEvent } from "react";
import { Alert, AppLink, Button } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { accountError, isCode } from "@/lib/accounts";
import { AuthHead } from "./auth-head";
import { PasswordField } from "./password-field";
import { PilotScreen } from "./pilot-screen";
import { useUrlToken } from "./use-url-token";
import { ResendLinkForm } from "./resend-link-form";

type Screen = "form" | "done" | "dead" | "full";

// /verify-email?token=...: the page the verification mail opens. Opening it does nothing; the
// button posts (a mail scanner that fetches the link must never confirm an account), and the
// visitor types the password they chose at sign-up again, because the link confirms only that
// sign-up's password (docs/fe-integration-guide-pilot-accounts.md section 5). It does not sign the
// person in: the next step is the sign-in page.
export function VerifyEmailForm() {
  // The token is read from the address bar in the browser and then removed from it (use-url-token.ts).
  const { ready, token } = useUrlToken("token");
  const [screen, setScreen] = useState<Screen>("form");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [formError, setFormError] = useState<string | null>(null);
  const [deadMessage, setDeadMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || !token) return;
    setFormError(null);
    if (!password) return setError("Enter the password you chose.");
    setError(undefined);
    setPending(true);
    try {
      await api.verifyEmail(token, password);
      setScreen("done");
    } catch (caught) {
      if (isCode(caught, "password_mismatch")) setError(accountError(caught));
      else if (isCode(caught, "token_invalid_or_expired")) {
        setDeadMessage(accountError(caught));
        setScreen("dead");
      } else if (isCode(caught, "pilot_full")) setScreen("full");
      else setFormError(accountError(caught));
    } finally {
      setPending(false);
    }
  }

  // No token once the address has been read: the link is missing, or the page was reloaded after
  // the token was taken out of the address.
  const missing = ready && !token && screen === "form";

  if (screen === "done") {
    return (
      <div className="auth-form" data-screen="email-confirmed">
        <AuthHead tile="check" tone="success" title="Email confirmed">
          Your account is ready. Sign in with the email and password you chose.
        </AuthHead>
        <Button href="/login" size="field" block>
          Go to sign in
        </Button>
      </div>
    );
  }

  if (screen === "full") {
    return (
      <PilotScreen
        kind="full"
        note="Your confirmation link was not used up. If a place opens while the link is still valid, it will work."
      />
    );
  }

  if (screen === "dead" || missing) {
    return (
      <div className="auth-form" data-screen="link-expired">
        <AuthHead tile="mail" tone="warning" title="This link does not work">
          {missing
            ? "This link is missing or was already used. Ask for a new one below."
            : (deadMessage ?? "This link has expired or was already used. Ask for a new one.")}
        </AuthHead>
        <ResendLinkForm />
        <p className="auth-small">
          Already confirmed? <AppLink href="/login">Sign in</AppLink>
        </p>
      </div>
    );
  }

  return (
    <form className="auth-form" method="post" onSubmit={submit} noValidate>
      <AuthHead tile="mail" title="Confirm your email">
        Enter the password you chose when you signed up. We ask again so only you can confirm this address.
      </AuthHead>
      {formError ? <Alert kind="error">{formError}</Alert> : null}
      <PasswordField
        id="password"
        label="Your password"
        autoComplete="current-password"
        placeholder="The password you chose"
        value={password}
        show={show}
        onToggle={() => setShow((value) => !value)}
        error={error}
        autoFocus
        onChange={setPassword}
      />
      <div className="auth-stack-sm">
        <Button size="field" block disabled={pending}>
          {pending ? "Confirming..." : "Confirm email"}
        </Button>
        <p className="auth-small">
          Signed up twice? Each link works only with the password from its own sign-up. Forgot it?
          <AppLink href="/signup">Create the account again</AppLink>
        </p>
      </div>
    </form>
  );
}
