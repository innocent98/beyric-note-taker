import { Landing, LandingFooter, LandingHeader } from "@/components/landing/landing";
import { JsonLd, organizationJsonLd, softwareJsonLd } from "@/components/site/json-ld";

// The whole landing page: the visitor's `/` and everyone's `/welcome` (app/welcome/page.tsx). The same
// words either way; `signedIn` only changes where the header and footer logo lead and what the header
// button says (see LandingHeader).
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
