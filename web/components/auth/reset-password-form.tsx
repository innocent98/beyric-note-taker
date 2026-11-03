"use client";

import { useState, type FormEvent } from "react";
import { Alert, Button } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { accountError, fieldOf, isCode, lengthProblem } from "@/lib/accounts";
import { AuthHead } from "./auth-head";
import { PasswordField, PasswordRules } from "./password-field";
import { useUrlToken } from "./use-url-token";

type Screen = "form" | "done" | "dead";

// /reset-password?token=...: the page the reset mail opens. A new password and the same again, then
// "Password updated". A weak password leaves the link usable (guide section 7), so the form stays;
// a dead link sends the visitor to ask for another. A reset signs the user out on every device and
// does not sign anyone in.
export function ResetPasswordForm() {
  // The token is read from the address bar in the browser and then removed from it (use-url-token.ts).
  const { ready, token } = useUrlToken("token");
  const [screen, setScreen] = useState<Screen>("form");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [show, setShow] = useState(false);
  const [errors, setErrors] = useState<{ password?: string; confirm?: string }>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [deadMessage, setDeadMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || !token) return;
    setFormError(null);
    const found: { password?: string; confirm?: string } = {};
    const tooShortOrLong = lengthProblem(password);
    if (tooShortOrLong) found.password = tooShortOrLong;
    if (!confirm) found.confirm = "Type the password again.";
    else if (confirm !== password) found.confirm = "Passwords do not match.";
    setErrors(found);
    if (Object.keys(found).length) return;
    setPending(true);
    try {
      await api.resetPassword(token, password);
      setPassword("");
      setConfirm("");
      setScreen("done");
    } catch (caught) {
      if (isCode(caught, "token_invalid_or_expired")) {
        setDeadMessage(accountError(caught));
        setScreen("dead");
      } else if (fieldOf(caught) === "password") setErrors({ password: accountError(caught) });
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
      <div className="auth-form" data-screen="password-updated">
        <AuthHead tile="check" tone="success" title="Password updated">
          You can now sign in with your new password. You were signed out on every device.
        </AuthHead>
        <Button href="/login" size="field" block>
          Back to sign in
        </Button>
      </div>
    );
  }

  if (screen === "dead" || missing) {
    return (
      <div className="auth-form" data-screen="reset-link-expired">
        <AuthHead tile="lock" tone="warning" title="This link does not work">
          {missing
            ? "This link is missing or was already used. Ask for a new one."
            : (deadMessage ?? "This link has expired or was already used. Ask for a new one.")}
        </AuthHead>
        <Button href="/forgot-password" size="field" block>
          Ask for a new link
        </Button>
      </div>
    );
  }

  return (
    <form className="auth-form" method="post" onSubmit={submit} noValidate>
      <AuthHead title="Choose a new password">You will be signed out on every device.</AuthHead>
      {formError ? <Alert kind="error">{formError}</Alert> : null}
      <div className="auth-fields">
        <div className="stack-xs">
          <PasswordField
            id="password"
            label="New password"
            autoComplete="new-password"
            placeholder="Create a password"
            value={password}
            show={show}
            onToggle={() => setShow((value) => !value)}
            error={errors.password}
            autoFocus
            onChange={setPassword}
          />
          <PasswordRules password={password} />
        </div>
        <PasswordField
          id="confirm"
          label="Confirm password"
          autoComplete="new-password"
          placeholder="Type it again"
          value={confirm}
          show={show}
          error={errors.confirm}
          onChange={setConfirm}
        />
      </div>
      <Button size="field" block disabled={pending}>
        {pending ? "Updating..." : "Update password"}
      </Button>
    </form>
  );
}
