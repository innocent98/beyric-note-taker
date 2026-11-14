import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { LoginForm } from "@/components/app/login-form";
import { AuthShell } from "@/components/site/auth-shell";
import { getPilot, getSession } from "@/lib/api/server";
import { LOGIN_ERRORS_WITH_SIGNUP, loginErrorMessage } from "@/lib/accounts";

export const metadata: Metadata = { title: "Sign in", robots: { index: false, follow: false } };

// `?error=<code>` is where the Google callback sends a visitor it turned back (guide section 9).
export default async function LoginPage({ searchParams }: { searchParams: Promise<{ error?: string | string[] }> }) {
  const session = await getSession();
  if (session?.authenticated) redirect("/meetings");
  const { error } = await searchParams;
  const code = Array.isArray(error) ? error[0] : error;
  const pilot = await getPilot();
  return (
    <AuthShell action={{ text: "New to Beyric?", label: "Create account", href: "/signup" }}>
      <LoginForm
        google={pilot?.google_signin === true}
        redirectError={loginErrorMessage(code)}
        redirectSignup={code !== undefined && LOGIN_ERRORS_WITH_SIGNUP.has(code)}
      />
    </AuthShell>
  );
}
