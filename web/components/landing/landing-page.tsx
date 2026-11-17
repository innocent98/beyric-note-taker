import { Landing } from "@/components/landing/landing";
import { JsonLd, organizationJsonLd, softwareJsonLd } from "@/components/site/json-ld";

// The landing page's content: the visitor's `/` and everyone's `/welcome` (app/(public)/welcome/page.tsx).
// The same words either way. The header and footer are the public shell's (see PublicShell), which
// changes only where the logo leads and what the header button says for the signed-in owner.
export function LandingPage() {
  return (
    <>
      <JsonLd data={[organizationJsonLd(), softwareJsonLd()]} />
      <Landing />
    </>
  );
}
