import type { Metadata } from "next";
import { UnsubscribeForm } from "@/components/auth/unsubscribe-form";
import { AuthShell } from "@/components/site/auth-shell";

export const metadata: Metadata = { title: "Stop emails", robots: { index: false, follow: false } };

// The page "Stop mail from Beyric Note Taker" in a shared-notes email opens
// (`/unsubscribe?token=...`). LEAD-DESIGNED, not in the designer's file: built from the
// verify-email page (the same shell, head and button). Opening it does nothing: a mail scanner that
// fetches the link must not opt anyone out, so the button posts. The token is read in the browser.
export default function UnsubscribePage() {
  return (
    <AuthShell action={{ label: "Back to sign in", href: "/login" }}>
      <UnsubscribeForm />
    </AuthShell>
  );
}
