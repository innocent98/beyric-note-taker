import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { DecisionsView } from "@/components/app/decisions-view";
import { getReviewOpen, load, requireOwner } from "@/lib/api/server";

export const metadata: Metadata = { title: "Decisions", robots: { index: false, follow: false } };

export default async function DecisionsPage() {
  const api = await requireOwner();
  const initial = await load(api.decisions.list());
  return (
    <AppShell current="decisions" reviewOpen={await getReviewOpen()}>
      <DecisionsView initial={initial} />
    </AppShell>
  );
}
