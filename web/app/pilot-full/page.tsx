import type { Metadata } from "next";
import { PilotScreen } from "@/components/auth/pilot-screen";
import { AuthShell } from "@/components/site/auth-shell";
import { Alert, Button } from "@/components/ui";
import { getPilot } from "@/lib/api/server";

export const metadata: Metadata = { title: "The pilot is full", robots: { index: false, follow: false } };

// Where the Google callback sends a new account that the full pilot turned away (guide section 9).
// No address is in the URL: a 15-minute cookie holds it and GET /api/pilot returns it as
// `prefill_email`, so the waiting list form starts filled in.
export default async function PilotFullPage() {
  const pilot = await getPilot();
  return (
    <AuthShell action={{ text: "Already have an account?", label: "Sign in", href: "/login" }}>
      {pilot?.state === "open" ? (
        <div className="auth-form">
          <Alert kind="info" role="status">
            A place has opened since you tried. You can create your account now.
          </Alert>
          <Button href="/signup" size="field" block>
            Create account
          </Button>
        </div>
      ) : (
        <PilotScreen kind={pilot?.state === "closed" ? "closed" : "full"} email={pilot?.prefill_email ?? undefined} />
      )}
    </AuthShell>
  );
}
