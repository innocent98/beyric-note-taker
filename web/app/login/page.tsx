import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { LoginForm } from "@/components/app/login-form";
import { AuthShell } from "@/components/site/auth-shell";
import { getSession } from "@/lib/api/server";

export const metadata: Metadata = { title: "Sign in", robots: { index: false, follow: false } };

export default async function LoginPage() {
  const session = await getSession();
  if (session?.authenticated) redirect("/");
  return (
    <AuthShell>
      <LoginForm />
    </AuthShell>
  );
}
