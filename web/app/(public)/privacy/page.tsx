import type { Metadata } from "next";
import { PrivacyContent } from "@/components/legal/privacy-content";
import { PRIVACY_TOC } from "@/components/legal/toc";
import { LegalPage } from "@/components/legal/legal-page";
import { audioRetentionDays, OG_IMAGES } from "@/lib/site";

const description =
  "How Beyric Note Taker collects, uses and shares information, including Google user data and meeting recordings.";

export const metadata: Metadata = {
  title: "Privacy Policy",
  description,
  alternates: { canonical: "/privacy" },
  openGraph: { title: "Privacy Policy - Beyric Note Taker", description, url: "/privacy", images: OG_IMAGES },
};

export default function PrivacyPage() {
  return (
    <LegalPage toc={PRIVACY_TOC}>
      <PrivacyContent days={audioRetentionDays()} />
    </LegalPage>
  );
}
