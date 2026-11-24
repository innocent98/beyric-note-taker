import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { cache } from "react";
import { AppShell } from "@/components/app/shell";
import { DocumentView } from "@/components/app/document-view";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, load, requireOwner, getShellTeams } from "@/lib/api/server";

type Props = { params: Promise<{ id: string }> };

// Ids are 1 to 10 digits; anything else is a 404 without asking FastAPI (which would say the same).
function parseId(raw: string): number {
  if (!/^\d{1,10}$/.test(raw)) notFound();
  return Number(raw);
}

// One fetch for the title and the page.
const fetchDocument = cache(async (id: number) => {
  const api = await requireOwner();
  return load(api.documents.get(id));
});

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const doc = await fetchDocument(parseId((await params).id));
  return { title: doc.title, robots: { index: false, follow: false } };
}

// A draft made from a meeting by a recipe (docs/fe-integration-guide-recipes.md). Another person's draft
// and one that does not exist are both a 404.
export default async function DocumentPage({ params }: Props) {
  const doc = await fetchDocument(parseId((await params).id));
  return (
    <AppShell
      current="meetings"
      reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()}
      unread={await getUnreadNotifications()} user={await getShellUser()} teams={await getShellTeams()}
      timeZone={await getTimeZone()}
    >
      {/* keyed by the draft: Regenerate lands on another id and must start from that draft's own state */}
      <DocumentView key={doc.id} initial={doc} />
    </AppShell>
  );
}
