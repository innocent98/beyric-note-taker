import type { Metadata } from "next";
import { VerifyEmailForm } from "@/components/auth/verify-email-form";
import { AuthShell } from "@/components/site/auth-shell";

export const metadata: Metadata = { title: "Confirm your email", robots: { index: false, follow: false } };

// The page the verification mail opens (`/verify-email?token=...`). A GET only shows the form: a
// mail scanner that opens the link must never confirm anything, so the confirmation is a POST the
// visitor starts with their password (guide sections 3 and 5). The token is not read here: the form
// takes it from the address bar in the browser and removes it from the address, so it is not in
// the server-rendered page.
export default function VerifyEmailPage() {
  return (
    <AuthShell action={{ label: "Back to sign in", href: "/login" }}>
      <VerifyEmailForm />
    </AuthShell>
  );
}
