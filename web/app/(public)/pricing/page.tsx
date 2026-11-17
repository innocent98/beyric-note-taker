import type { Metadata } from "next";
import { HOURS, PACK, PLAN, TRIAL } from "@/components/pricing/content";
import { Pricing } from "@/components/pricing/pricing";
import { getSession } from "@/lib/api/server";
import { OG_IMAGES } from "@/lib/site";

const description = `Beyric Note Taker pricing: a one-time trial of ${HOURS(TRIAL.hours)} valid ${TRIAL.days} days, then one monthly plan of ${HOURS(PLAN.hours)} at ${PLAN.usd} or ${PLAN.ngn} a month, and ${PACK.hours}-hour packs. Meeting hours belong to the person, not the team.`;

export const metadata: Metadata = {
  title: "Pricing",
  description,
  alternates: { canonical: "/pricing" },
  openGraph: { title: "Pricing - Beyric Note Taker", description, url: "/pricing", images: OG_IMAGES },
};

// A public page for everyone, the owner included; the header and footer are the public layout's. A signed-in
// person's buttons lead to the plans screen instead of the sign-up page.
export default async function Page() {
  const signedIn = (await getSession())?.authenticated === true;
  return <Pricing signedIn={signedIn} />;
}
