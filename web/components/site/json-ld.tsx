import { LEGAL_NAME, SITE_DESCRIPTION, SITE_NAME, SITE_URL } from "@/lib/site";

// Structured data for search and answer engines. Every claim here has to be true of the pilot
// today (see docs/sop/2026-11-02-public-pages.md and docs/sop/2026-11-14-landing-security-copy.md):
// free during the pilot, with an hours limit.
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
    offers: { "@type": "Offer", price: "0", priceCurrency: "USD", description: "Free during the pilot, with an hours limit on each account" },
    publisher: { "@type": "Organization", name: LEGAL_NAME },
  };
}

// `<` is escaped so a value can never close the script element.
export function JsonLd({ data }: { data: object | object[] }) {
  const json = JSON.stringify(data).replace(/</g, "\\u003c");
  return <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: json }} />;
}
