import type { Metadata } from "next";
import { TermsContent } from "@/components/legal/terms-content";
import { TERMS_TOC } from "@/components/legal/toc";
import { LegalShell } from "@/components/site/legal-shell";
import { OG_IMAGES } from "@/lib/site";

const description =
  "The rules for using Beyric Note Taker, including your responsibilities when recording meetings.";

export const metadata: Metadata = {
  title: "Terms of Service",
  description,
  alternates: { canonical: "/terms" },
  openGraph: { title: "Terms of Service - Beyric Note Taker", description, url: "/terms", images: OG_IMAGES },
};

export default function TermsPage() {
  return (
    <LegalShell current="terms" toc={TERMS_TOC}>
      <TermsContent />
    </LegalShell>
  );
}
