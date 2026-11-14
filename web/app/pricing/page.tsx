import type { Metadata } from "next";
import { PricingPage } from "@/components/pricing/pricing";
import { getSession } from "@/lib/api/server";
import { OG_IMAGES } from "@/lib/site";

const description =
  "The plans Beyric Note Taker expects to offer: a one-time trial, then monthly plans with meeting hours shared by the whole team. Prices and hours are still to be announced.";

export const metadata: Metadata = {
  title: "Pricing",
  description,
  alternates: { canonical: "/pricing" },
  openGraph: { title: "Pricing - Beyric Note Taker", description, url: "/pricing", images: OG_IMAGES },
};

// A public page for everyone, the owner included (the header then offers "Open the app", as /welcome does).
export default async function Page() {
  const signedIn = (await getSession())?.authenticated === true;
  return <PricingPage signedIn={signedIn} />;
}
