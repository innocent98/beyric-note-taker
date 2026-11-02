import type { Metadata } from "next";
import { TermsContent } from "@/components/legal/terms-content";
import { PublicShell } from "@/components/site/public-shell";

const description =
  "The rules for using Beyric Note Taker, including your responsibilities when recording meetings.";

export const metadata: Metadata = {
  title: "Terms of Service",
  description,
  alternates: { canonical: "/terms" },
  openGraph: { title: "Terms of Service - Beyric Note Taker", description, url: "/terms" },
};

export default function TermsPage() {
  return (
    <PublicShell mainClass="pub-main legal">
      <TermsContent />
    </PublicShell>
  );
}
