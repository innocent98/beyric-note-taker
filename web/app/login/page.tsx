import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { LoginForm } from "@/components/app/login-form";
import { Card, Logo } from "@/components/ui";
import { getSession } from "@/lib/api/server";

export const metadata: Metadata = { title: "Sign in", robots: { index: false, follow: false } };

export default async function LoginPage() {
  const session = await getSession();
  if (session?.authenticated) redirect("/");
  return (
    <div className="auth">
      <div className="auth-head">
        <Logo large href={null} />
        <h1 className="auth-title">Sign in to Beyric</h1>
        <p className="auth-sub">Welcome back.</p>
      </div>
      <div className="auth-card">
        <Card>
          <LoginForm />
        </Card>
      </div>
    </div>
  );
}
