import type { Metadata } from "next";
import { LandingPage } from "@/components/landing/landing-page";
import { getSession } from "@/lib/api/server";

// The landing page for everyone, the owner included: `/` is the owner's Meetings page, so without this
// an owner could never read it. The words are the same as the visitor's `/`. Search: the canonical is
// `/`, so the two are one page to a crawler (a crawler is never signed in, so it reads exactly what `/`
// gives it); /welcome is not in the sitemap and is not blocked in robots.txt, because a blocked page's
// canonical would never be read. No `noindex` as well: a canonical plus a noindex is a mixed signal.
export const metadata: Metadata = {
  title: { absolute: "Beyric Note Taker - clear notes from every meeting" },
  alternates: { canonical: "/" },
};

export default async function WelcomePage() {
  const signedIn = (await getSession())?.authenticated === true;
  return <LandingPage signedIn={signedIn} />;
}
