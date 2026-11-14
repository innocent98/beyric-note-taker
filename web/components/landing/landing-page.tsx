import { Landing, LandingFooter, LandingHeader } from "@/components/landing/landing";
import { JsonLd, organizationJsonLd, softwareJsonLd } from "@/components/site/json-ld";

// The whole landing page, at `/` for everyone (app/page.tsx). The same words for a visitor and for the
// owner; `signedIn` only changes the header button: "Open the app" (to `/meetings`) instead of Sign in
// and Get started (see LandingHeader).
export function LandingPage({ signedIn = false }: { signedIn?: boolean }) {
  return (
    <div className="ld">
      <JsonLd data={[organizationJsonLd(), softwareJsonLd()]} />
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <LandingHeader signedIn={signedIn} />
      <main id="main">
        <Landing />
      </main>
      <LandingFooter signedIn={signedIn} />
    </div>
  );
}
