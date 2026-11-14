import { LEGAL_NAME, SITE_DESCRIPTION, SITE_NAME, SITE_URL } from "@/lib/site";

// Structured data for search and answer engines. Every claim here has to be true of the pilot
// today (see docs/sop/2026-11-02-public-pages.md and docs/sop/2026-11-14-landing-v2-and-pricing.md).
// No `offers`: no price is decided and the pilot is not described as free, so no price is published.
export function organizationJsonLd() {
  return {
    "@context": "https://schema.org",
    "@type": "Organization",
    name: LEGAL_NAME,
    url: SITE_URL,
    logo: `${SITE_URL}/icons/icon-512.png`,
    address: { "@type": "PostalAddress", addressLocality: "Lagos", addressCountry: "NG" },
    contactPoint: { "@type": "ContactPoint", email: "privacy@beyrictech.com", contactType: "privacy" },
  };
}

export function softwareJsonLd() {
  return {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: SITE_NAME,
    url: SITE_URL,
    description: SITE_DESCRIPTION,
    applicationCategory: "BusinessApplication",
    operatingSystem: "Web",
    publisher: { "@type": "Organization", name: LEGAL_NAME },
  };
}

// `<` is escaped so a value can never close the script element.
export function JsonLd({ data }: { data: object | object[] }) {
  const json = JSON.stringify(data).replace(/</g, "\\u003c");
  return <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: json }} />;
}
