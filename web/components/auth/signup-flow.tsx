"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Alert, AppLink, Button, Checkbox, Field } from "@/components/ui";
import { api } from "@/lib/api/browser";
import {
  lengthProblem,
  RESEND_WAIT_SECONDS,
  accountError,
  fieldOf,
  isCode,
  validEmail,
} from "@/lib/accounts";
import type { PilotState } from "@/lib/api/types";
import { AuthHead } from "./auth-head";
import { GoogleButton } from "./google-button";
import { InviteBanner, LockedEmail } from "./invite-banner";
import { MailLinks } from "./mail-links";
import { PasswordField, PasswordRules } from "./password-field";
import { PilotScreen } from "./pilot-screen";
import { RECORDING_ACK_MESSAGE, RecordingBox } from "./recording-consent";
import { useCountdown } from "./use-countdown";
import { useUrlToken } from "./use-url-token";

type Screen = "form" | "sent" | "closed" | "full";
type Errors = { name?: string; email?: string; password?: string; terms?: string; recording?: string };

const TERMS_MESSAGE = "Accept the Terms and the Privacy Policy to create an account.";

// /signup. `pilot` is GET /api/pilot as the server read it: `closed` and `full` draw their own
// screens with the waiting list (Auth.dc.html has neither); `open` draws the sign-up form. A visitor
// with an invitation (`invite`, the token in the mailed link) gets the form whatever the state,
// because the API accepts an invited address while the pilot is closed or full (guide section 4).
// The API also decides at submit time: a 403 `pilot_closed` or 409 `pilot_full` switches the screen.
// Sign-up never says whether an address is registered: every accepted answer is "check your email".
export function SignupFlow({
  pilot,
  google,
  hasInvite,
  prefillEmail,
}: {
  pilot: PilotState | null;
  google: boolean;
  // The address has `?invite=`. The token itself is read in the browser and taken out of the
  // address (use-url-token.ts), so the server never puts it in the page.
  hasInvite: boolean;
  prefillEmail?: string | null;
}) {
  const { ready, token: invite } = useUrlToken("invite");
  // Until the address has been read, trust the server; after it, an invitation needs its token.
  const invited = hasInvite && (!ready || invite !== null);
  const initial: Screen = hasInvite ? "form" : pilot === "closed" ? "closed" : pilot === "full" ? "full" : "form";
  const [screen, setScreen] = useState<Screen>(initial);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  // A team's invitation names its address: when the link is one, the email is filled in and locked
  // to it (the invitation works for that address only), and the banner says who invited you.
  const [teamInvite, setTeamInvite] = useState<{ email: string; by: string | null; team: string } | null>(null);
  const [password, setPassword] = useState("");
  const [accepted, setAccepted] = useState(false);
  // The recording box: never pre-ticked, and the API refuses a sign-up without it.
  const [recording, setRecording] = useState(false);
  const [show, setShow] = useState(false);
  const [errors, setErrors] = useState<Errors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [sentTo, setSentTo] = useState("");
  const [resendNote, setResendNote] = useState<string | null>(null);
  const [resending, setResending] = useState(false);
  const wait = useCountdown(RESEND_WAIT_SECONDS);
  useEffect(() => {
    if (!invite) return;
    let cancelled = false;
    api.team
      .preview(invite)
      .then((preview) => {
        if (cancelled) return;
        setTeamInvite({ email: preview.email, by: preview.inviter_name, team: preview.team_name });
        setEmail(preview.email);
      })
      .catch(() => {
        // not a team link (a waiting-list invitation): the form works as it always did
      });
    return () => {
      cancelled = true;
    };
  }, [invite]);
  const termsRef = useRef<HTMLInputElement>(null);
  const recordingRef = useRef<HTMLInputElement>(null);

  function validate(): Errors {
    const found: Errors = {};
    if (!name.trim()) found.name = "Enter your name.";
    if (!email.trim()) found.email = "Enter your work email.";
    else if (!validEmail(email)) found.email = "Enter a valid email, like you@company.com.";
    const tooShortOrLong = lengthProblem(password);
    if (tooShortOrLong) found.password = tooShortOrLong;
    if (!accepted) found.terms = TERMS_MESSAGE;
    if (!recording) found.recording = RECORDING_ACK_MESSAGE;
    return found;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setFormError(null);
    const found = validate();
    setErrors(found);
    if (Object.keys(found).length) return;
    setPending(true);
    try {
      await api.signup({
        name: name.trim(),
        email: email.trim(),
        password,
        accept_terms: true,
        accept_recording: true,
        ...(invite ? { invite_token: invite } : {}),
      });
      setSentTo(email.trim());
      setPassword("");
      setResendNote(null);
      wait.start(RESEND_WAIT_SECONDS);
      setScreen("sent");
    } catch (caught) {
      if (isCode(caught, "pilot_closed")) return setScreen("closed");
      if (isCode(caught, "pilot_full")) return setScreen("full");
      const field = fieldOf(caught);
      if (field) setErrors({ [field]: accountError(caught) });
      else setFormError(accountError(caught));
    } finally {
      setPending(false);
    }
  }

  // Google needs both boxes ticked first: they are what lets a NEW account be created (guide section 9).
  function googleGuard(): boolean {
    if (accepted && recording) return true;
    setErrors((current) => ({
      ...current,
      ...(accepted ? {} : { terms: TERMS_MESSAGE }),
      ...(recording ? {} : { recording: RECORDING_ACK_MESSAGE }),
    }));
    (accepted ? recordingRef : termsRef).current?.focus();
    return false;
  }

  async function resend() {
    if (resending || wait.left > 0) return;
    setResending(true);
    setResendNote(null);
    try {
      await api.resendVerification(sentTo);
      setResendNote("If that address is waiting to be confirmed, a new link is on its way. It asks for the same password as before.");
      wait.start(RESEND_WAIT_SECONDS);
    } catch (caught) {
      setResendNote(accountError(caught));
    } finally {
      setResending(false);
    }
  }

  if (screen === "closed" || screen === "full") {
    return <PilotScreen kind={screen} email={email.trim() || prefillEmail || undefined} />;
  }

  if (screen === "sent") {
    return (
      <div className="auth-form" data-screen="check-email">
        <AuthHead tile="mail" title="Check your email">
          We will email a confirmation link to <span className="auth-who">{sentTo}</span>. It can take a few minutes. Open
          it and enter the password you just chose.
        </AuthHead>
        <MailLinks />
        <div className="auth-stack-sm">
          <div className="auth-small">
            Did not get it? Check spam, or
            {wait.left > 0 ? (
              <span className="wait">resend in {wait.label}</span>
            ) : (
              <Button kind="link" type="button" onClick={resend} disabled={resending}>
                {resending ? "sending the link..." : "resend the link"}
              </Button>
            )}
          </div>
          {resendNote ? (
            <p className="auth-small" role="status">
              {resendNote}
            </p>
          ) : null}
          <div className="auth-small">
            Wrong email?
            <Button kind="link" type="button" onClick={() => setScreen("form")}>
              Change it
            </Button>
          </div>
        </div>
      </div>
    );
  }

  // A team's invitation is the designer's "Join your team" screen (Auth.dc.html:171-208): banner,
  // locked email, name, password, "Join Beyric". The same state and the same sign-up call as the
  // form below; the terms and recording boxes stay because the API needs `accept_terms` and `accept_recording`, and the Google button is
  // not drawn (that sign-up does not carry the invitation).
  if (teamInvite) {
    return (
      <form className="auth-form" method="post" onSubmit={submit} noValidate data-screen="join-team">
        <InviteBanner by={teamInvite.by} team={teamInvite.team} />
        <AuthHead title="Join your team">You will see shared meeting notes and the tasks assigned to you.</AuthHead>
        {formError ? <Alert kind="error">{formError}</Alert> : null}
        <div className="auth-fields">
          <LockedEmail email={teamInvite.email} />
          <Field
            id="name"
            label="Full name"
            large
            autoComplete="name"
            placeholder="Your name"
            value={name}
            error={errors.name}
            onChange={(event) => setName(event.target.value)}
          />
          <div className="stack-xs">
            <PasswordField
              id="password"
              label="Password"
              autoComplete="new-password"
              placeholder="Create a password"
              value={password}
              show={show}
              onToggle={() => setShow((value) => !value)}
              error={errors.password}
              onChange={setPassword}
            />
            <PasswordRules password={password} />
          </div>
          {errors.email ? <Alert kind="error">{errors.email}</Alert> : null}
        </div>
        <div className="auth-stack-sm">
          <Checkbox
            id="accept_terms"
            ref={termsRef}
            checked={accepted}
            error={errors.terms}
            onChange={(event) => setAccepted(event.target.checked)}
          >
            I agree to the{" "}
            <AppLink href="/terms" target="_blank">
              Terms
            </AppLink>{" "}
            and the{" "}
            <AppLink href="/privacy" target="_blank">
              Privacy Policy
            </AppLink>
            .
          </Checkbox>
          <RecordingBox ref={recordingRef} checked={recording} error={errors.recording} onChange={setRecording} />
          <Button size="field" block disabled={pending}>
            {pending ? "Joining..." : "Join Beyric"}
          </Button>
        </div>
      </form>
    );
  }

  return (
    <form className="auth-form" method="post" onSubmit={submit} noValidate>
      <AuthHead title="Create your account">Send the note taker to your next meeting in a few minutes.</AuthHead>
      {invited ? (
        <Alert kind="info" role="status">
          You have an invitation to the pilot. Use the email address it was sent to: the invitation works for that
          address only.
        </Alert>
      ) : null}
      {google ? (
        <div className="auth-sso">
          <GoogleButton acceptTerms={accepted} acceptRecording={recording} guard={googleGuard} />
          <div className="auth-or">or use your email</div>
        </div>
      ) : null}
      {formError ? <Alert kind="error">{formError}</Alert> : null}
      <div className="auth-fields">
        <Field
          id="name"
          label="Full name"
          large
          autoComplete="name"
          placeholder="Ada Okafor"
          value={name}
          error={errors.name}
          onChange={(event) => setName(event.target.value)}
        />
        <Field
          id="email"
          label="Work email"
          type="email"
          large
          autoComplete="email"
          placeholder="you@company.com"
          value={email}
          error={errors.email}
          onChange={(event) => setEmail(event.target.value)}
        />
        <div className="stack-xs">
          <PasswordField
            id="password"
            label="Password"
            autoComplete="new-password"
            placeholder="Create a password"
            value={password}
            show={show}
            onToggle={() => setShow((value) => !value)}
            error={errors.password}
            onChange={setPassword}
          />
          <PasswordRules password={password} />
        </div>
      </div>
      <div className="auth-stack-sm">
        <Checkbox
          id="accept_terms"
          ref={termsRef}
          checked={accepted}
          error={errors.terms}
          onChange={(event) => setAccepted(event.target.checked)}
        >
          I agree to the{" "}
          <AppLink href="/terms" target="_blank">
            Terms
          </AppLink>{" "}
          and the{" "}
          <AppLink href="/privacy" target="_blank">
            Privacy Policy
          </AppLink>
          .
        </Checkbox>
        <RecordingBox ref={recordingRef} checked={recording} error={errors.recording} onChange={setRecording} />
        <Button size="field" block disabled={pending}>
          {pending ? "Creating account..." : "Create account"}
        </Button>
      </div>
    </form>
  );
}
