import type { Metadata } from "next";
import { SUMMARY } from "@/components/pricing/content";
import { Pricing } from "@/components/pricing/pricing";
import { getSession, serverApi } from "@/lib/api/server";
import { OG_IMAGES } from "@/lib/site";

const description = `Beyric Note Taker pricing: ${SUMMARY}. Meeting hours belong to the person, not the team.`;

export const metadata: Metadata = {
  title: "Pricing",
  description,
  alternates: { canonical: "/pricing" },
  openGraph: { title: "Pricing - Beyric Note Taker", description, url: "/pricing", images: OG_IMAGES },
};

// Does billing answer for this signed-in person? The same question the app's billing entry points ask: a 404 (off,
// or not on the allow-list) or any failure means no, and the page then does not send them to the plans screen.
async function billingAnswers(): Promise<boolean> {
  try {
    await (await serverApi()).billing.state();
    return true;
  } catch {
    return false;
  }
}

// A public page for everyone, the owner included; the header and footer are the public layout's. The buttons lead a
// signed-in person to the plans screen only when billing answers for them (else to the app), a visitor to sign-up.
export default async function Page() {
  const signedIn = (await getSession())?.authenticated === true;
  return <Pricing signedIn={signedIn} billingOn={signedIn && (await billingAnswers())} />;
}
