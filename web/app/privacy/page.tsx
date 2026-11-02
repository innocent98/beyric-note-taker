import type { Metadata } from "next";
import { PrivacyContent } from "@/components/legal/privacy-content";
import { PublicShell } from "@/components/site/public-shell";
import { audioRetentionDays } from "@/lib/site";

const description =
  "How Beyric Note Taker collects, uses and shares information, including Google user data and meeting recordings.";

export const metadata: Metadata = {
  title: "Privacy Policy",
  description,
  alternates: { canonical: "/privacy" },
  openGraph: { title: "Privacy Policy - Beyric Note Taker", description, url: "/privacy" },
};

export default function PrivacyPage() {
  return (
    <PublicShell mainClass="pub-main legal">
      <PrivacyContent days={audioRetentionDays()} />
    </PublicShell>
  );
}
