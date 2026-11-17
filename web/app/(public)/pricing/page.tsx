import type { Metadata } from "next";
import { Pricing } from "@/components/pricing/pricing";
import { OG_IMAGES } from "@/lib/site";

const description =
  "The plans Beyric Note Taker expects to offer: a one-time trial, then monthly plans whose meeting hours belong to the person, and extra-hour packs. Prices and hours are still to be announced.";

export const metadata: Metadata = {
  title: "Pricing",
  description,
  alternates: { canonical: "/pricing" },
  openGraph: { title: "Pricing - Beyric Note Taker", description, url: "/pricing", images: OG_IMAGES },
};

// A public page for everyone, the owner included; the header and footer are the public layout's.
export default function Page() {
  return <Pricing />;
}
