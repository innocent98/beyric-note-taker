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
  if ((await getSession())?.authenticated) redirect("/meetings");
  const { invite } = await searchParams;
  // Only whether there is an invitation: the token is read in the browser and removed from the
  // address, so it is not in the server-rendered page.
  const hasInvite = Boolean(Array.isArray(invite) ? invite[0] : invite);
  const pilot = await getPilot();
  return (
    <AuthShell action={{ text: hasInvite ? "Have an account?" : "Already have an account?", label: "Sign in", href: "/login" }}>
      <SignupFlow
        pilot={pilot?.state ?? null}
        google={pilot?.google_signin === true}
        hasInvite={hasInvite}
        prefillEmail={pilot?.prefill_email}
      />
    </AuthShell>
  );
}
