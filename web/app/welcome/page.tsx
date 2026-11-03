import type { Metadata } from "next";
import { JsonLd, organizationJsonLd, softwareJsonLd } from "@/components/site/json-ld";
import { PublicShell } from "@/components/site/public-shell";
import { Landing, LandingBand, LandingClose } from "@/components/landing/landing";

// The new landing page, mounted at /welcome (noindex) for review. `/` stays with FastAPI because
// it is the owner app when logged in; the landing moves to `/` once GET /api/session can say who
// is looking (then drop `robots` here and move the canonical to "/").
export const metadata: Metadata = {
  title: { absolute: "Beyric Note Taker - clear notes from every meeting" },
  robots: { index: false, follow: false },
};

export default function WelcomePage() {
  return (
    <>
      <JsonLd data={[organizationJsonLd(), softwareJsonLd()]} />
      <PublicShell mainClass="">
        <div className="pub-main">
          <Landing />
        </div>
        <LandingBand />
        <div className="pub-main">
          <LandingClose />
        </div>
      </PublicShell>
    </>
  );
}
