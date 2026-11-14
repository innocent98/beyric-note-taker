import type { Metadata } from "next";
import { LandingPage } from "@/components/landing/landing-page";
import { getSession } from "@/lib/api/server";

// `/` is the landing page for everyone, signed in or not: the same words, indexed, in the sitemap,
// canonical `/`. A signed-in owner sees the same page with one "Open the app" button in the header
// that leads to their Meetings page, `/meetings` (app/meetings/page.tsx). GET /api/session says
// which header to draw; a crawler is never signed in, so it reads the visitor's header.
export const metadata: Metadata = {
  title: { absolute: "Beyric Note Taker - clear notes from every meeting" },
  alternates: { canonical: "/" },
};

export default async function HomePage() {
  const signedIn = (await getSession())?.authenticated === true;
  return <LandingPage signedIn={signedIn} />;
}
