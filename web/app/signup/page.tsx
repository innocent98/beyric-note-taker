import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { SignupFlow } from "@/components/auth/signup-flow";
import { AuthShell } from "@/components/site/auth-shell";
import { getPilot, getSession } from "@/lib/api/server";

export const metadata: Metadata = { title: "Create your account", robots: { index: false, follow: false } };

// `?invite=<token>` is the waiting-list invitation mail's link (guide section 3). GET /api/pilot
// says whether the pilot is open, full or closed and whether Google sign-in exists. The sign-up
// itself stays closed until the admin opens it: nothing here opens it.
export default async function SignupPage({ searchParams }: { searchParams: Promise<{ invite?: string | string[] }> }) {
  if ((await getSession())?.authenticated) redirect("/");
  const { invite } = await searchParams;
  const token = (Array.isArray(invite) ? invite[0] : invite) || null;
  const pilot = await getPilot();
  return (
    <AuthShell action={{ text: "Already have an account?", label: "Sign in", href: "/login" }}>
      <SignupFlow
        pilot={pilot?.state ?? null}
        google={pilot?.google_signin === true}
        invite={token}
        prefillEmail={pilot?.prefill_email}
      />
    </AuthShell>
  );
}
