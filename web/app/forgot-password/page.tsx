import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ForgotPasswordFlow } from "@/components/auth/forgot-password-flow";
import { AuthShell } from "@/components/site/auth-shell";
import { getSession } from "@/lib/api/server";

export const metadata: Metadata = { title: "Reset your password", robots: { index: false, follow: false } };

export default async function ForgotPasswordPage() {
  if ((await getSession())?.authenticated) redirect("/");
  return (
    <AuthShell action={{ label: "Back to sign in", href: "/login" }}>
      <ForgotPasswordFlow />
    </AuthShell>
  );
}
