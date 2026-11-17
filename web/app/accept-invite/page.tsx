import type { Metadata } from "next";
import { AcceptInvite } from "@/components/auth/accept-invite";
import { AuthShell } from "@/components/site/auth-shell";

export const metadata: Metadata = { title: "Accept your invitation", robots: { index: false, follow: false } };

// The page the "Team invite" mail opens (`/accept-invite?token=...`). A GET only reads what the
// link is for (the public preview); joining is a POST the person starts with a button. The token is
// read in the browser and removed from the address (use-url-token.ts), so it is not in the page.
export default function AcceptInvitePage() {
  return (
    <AuthShell action={{ label: "Back to sign in", href: "/login" }}>
      <AcceptInvite />
    </AuthShell>
  );
}
