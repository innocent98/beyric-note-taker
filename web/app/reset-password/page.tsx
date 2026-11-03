import type { Metadata } from "next";
import { ResetPasswordForm } from "@/components/auth/reset-password-form";
import { AuthShell } from "@/components/site/auth-shell";

export const metadata: Metadata = { title: "Choose a new password", robots: { index: false, follow: false } };

// The page the reset mail opens (`/reset-password?token=...`), also the target of the link the admin
// can make when mail is not working (guide sections 3, 7 and 12).
export default async function ResetPasswordPage({ searchParams }: { searchParams: Promise<{ token?: string | string[] }> }) {
  const { token } = await searchParams;
  return (
    <AuthShell action={{ label: "Back to sign in", href: "/login" }}>
      <ResetPasswordForm token={(Array.isArray(token) ? token[0] : token) || null} />
    </AuthShell>
  );
}
