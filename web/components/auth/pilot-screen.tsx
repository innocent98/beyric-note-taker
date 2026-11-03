import { AppLink } from "@/components/ui";
import { AuthHead } from "./auth-head";
import { WaitlistForm } from "./waitlist-form";

// The two screens for a pilot that takes no new accounts, from GET /api/pilot (`closed`, `full`),
// or from the API's own refusal (403 `pilot_closed`, 409 `pilot_full`). Auth.dc.html draws neither,
// so they are built from its pieces: the icon tile, the title and the form. Both offer the waiting
// list, as the guide says (section 2). The words under each title are the API's own messages.
export function PilotScreen({
  kind,
  email,
  note,
}: {
  kind: "closed" | "full";
  email?: string;
  // One more line, for a visitor who arrives with a confirmation link that is still good.
  note?: string;
}) {
  const closed = kind === "closed";
  return (
    <div className="auth-form" data-screen={closed ? "closed" : "pilot-full"}>
      <AuthHead tile={closed ? "lock" : "info"} tone="warning" title={closed ? "Sign-up is not open yet" : "The pilot is full"}>
        {closed
          ? "Check back soon, or leave your email and we will write when it opens."
          : "Leave your email and we will write when a place opens."}
      </AuthHead>
      {note ? <p className="auth-small">{note}</p> : null}
      <WaitlistForm initialEmail={email} />
      <p className="auth-small">
        Already have an account? <AppLink href="/login">Sign in</AppLink>
      </p>
    </div>
  );
}
