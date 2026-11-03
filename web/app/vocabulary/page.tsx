import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { VocabularyManager } from "@/components/app/vocabulary-manager";
import { load, requireOwner } from "@/lib/api/server";

export const metadata: Metadata = { title: "Vocabulary", robots: { index: false, follow: false } };

export default async function VocabularyPage() {
  const api = await requireOwner();
  const terms = await load(api.vocabulary.list());
  return (
    <AppShell current="vocabulary">
      <VocabularyManager initial={terms} />
    </AppShell>
  );
}
