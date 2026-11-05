"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Alert, AppLink, Button } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { InvitePreview } from "@/lib/api/types";
import { AuthHead } from "./auth-head";
import { useUrlToken } from "./use-url-token";

type Who = { signedIn: boolean; email: string | null };
type Screen =
  | { kind: "loading" }
  | { kind: "dead" }
  | { kind: "ready"; preview: InvitePreview; who: Who };

// /accept-invite?token=...: where the "Team invite" mail's button goes. LEAD-DESIGNED, not in the
// designer's file (it drew the mail, not this page): built from the account screens' own frame and
// head (docs/fe-integration-guide-team.md, "The invitation link page"). It asks the public preview
// what the link is for, then:
//  * an address with no account is sent to sign up with the link (`/signup?invite=`), where the
//    email is filled in and locked;
//  * an existing account that is signed in as the invited address can accept, after being told its
//    own team is replaced;
//  * anyone else is told which address the invitation is for.
// A dead link (wrong, used, expired, revoked, anyone's) has one answer. The token is read from the
// address bar in the browser and removed from it (use-url-token.ts).
export function AcceptInvite() {
  const { ready, token } = useUrlToken("token");
  const router = useRouter();
  const [screen, setScreen] = useState<Screen>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ready || !token) return;
    let cancelled = false;
    (async () => {
      try {
        const [preview, session] = await Promise.all([api.team.preview(token), api.session()]);
        if (cancelled) return;
        setScreen({
          kind: "ready",
          preview,
          who: { signedIn: session.authenticated, email: session.user?.email ?? null },
        });
      } catch (caught) {
        if (cancelled) return;
        if (caught instanceof ApiError && caught.status !== 400) setError(errorMessage(caught));
        setScreen({ kind: "dead" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready, token]);

  async function accept() {
    if (busy || !token) return;
    setBusy(true);
    setError(null);
    try {
      await api.team.accept(token);
      router.push("/team");
    } catch (caught) {
      setError(errorMessage(caught));
      setBusy(false);
    }
  }

  // No token once the address has been read: the link is missing, or the page was reloaded after the
  // token was taken out of the address.
  const missing = ready && !token;

  if (screen.kind === "loading" && !missing) {
    return (
      <div className="auth-form" data-screen="invite-loading">
        <AuthHead title="Checking your invitation">One moment.</AuthHead>
      </div>
    );
  }

  if (screen.kind === "dead" || missing || screen.kind === "loading") {
    return (
      <div className="auth-form" data-screen="invite-dead">
        <AuthHead tile="mail" tone="warning" title="This invitation does not work">
          It is not valid any more: it may have expired, been used or been cancelled. Ask the person who invited you to
          send a new one.
        </AuthHead>
        {error ? <Alert kind="error">{error}</Alert> : null}
        <p className="auth-small">
          <AppLink href="/login">Go to sign in</AppLink>
        </p>
      </div>
    );
  }

  const { preview, who } = screen;
  const heading = preview.inviter_name
    ? `${preview.inviter_name} invited you to join ${preview.team_name}`
    : `You are invited to join ${preview.team_name}`;

  if (!preview.has_account) {
    return (
      <div className="auth-form" data-screen="invite-new">
        <AuthHead tile="mail" title={heading}>
          Create your Beyric Note Taker account with {preview.email} to join. You will confirm that address by email.
        </AuthHead>
        <Button href={`/signup?invite=${encodeURIComponent(token ?? "")}`} size="field" block>
          Create your account
        </Button>
        <p className="auth-small">
          Already have an account with this address? <AppLink href="/login">Sign in</AppLink>, then open the link in your
          email again.
        </p>
      </div>
    );
  }

  if (!who.signedIn) {
    return (
      <div className="auth-form" data-screen="invite-signin">
        <AuthHead tile="mail" title={heading}>
          Sign in as {preview.email} to accept, then open the link in your email again.
        </AuthHead>
        <Button href="/login" size="field" block>
          Go to sign in
        </Button>
      </div>
    );
  }

  if (who.email !== preview.email) {
    return (
      <div className="auth-form" data-screen="invite-wrong-account">
        <AuthHead tile="mail" tone="warning" title="This invitation is for another address">
          It was sent to {preview.email}, and you are signed in as {who.email}. Sign out, sign in with {preview.email}, then
          open the link in your email again.
        </AuthHead>
        <form method="post" action="/logout">
          <Button kind="secondary" size="field" block>
            Sign out
          </Button>
        </form>
      </div>
    );
  }

  return (
    <div className="auth-form" data-screen="invite-accept">
      <AuthHead tile="mail" title={heading}>
        Join as {preview.role === "admin" ? "an admin" : "a member"} to see shared meeting notes and the tasks assigned to
        you.
      </AuthHead>
      <Alert kind="notice">
        Your own team will be replaced by this one. Your tasks move to it. Your meetings stay yours and private, and
        nothing of yours is shared until you choose to.
      </Alert>
      {error ? <Alert kind="error">{error}</Alert> : null}
      <Button type="button" size="field" block disabled={busy} onClick={() => void accept()}>
        {busy ? "Joining..." : "Accept invitation"}
      </Button>
    </div>
  );
}
