import type { Metadata } from "next";
import { ResetPasswordForm } from "@/components/auth/reset-password-form";
import { AuthShell } from "@/components/site/auth-shell";

export const metadata: Metadata = { title: "Choose a new password", robots: { index: false, follow: false } };

// The page the reset mail opens (`/reset-password?token=...`), also the target of the link the admin
// can make when mail is not working (guide sections 3, 7 and 12). As on /verify-email, the token is
// read in the browser and removed from the address, not read here.
export default function ResetPasswordPage() {
  return (
    <AuthShell action={{ label: "Back to sign in", href: "/login" }}>
      <ResetPasswordForm />
    </AuthShell>
  );
}
