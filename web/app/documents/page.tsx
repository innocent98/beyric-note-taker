import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { DocumentsView, type DocumentsTab } from "@/components/app/documents-view";
import { DOCUMENT_PAGE } from "@/lib/documents";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, load, requireOwner, getShellTeams } from "@/lib/api/server";

export const metadata: Metadata = { title: "Documents", robots: { index: false, follow: false } };

// The drafts the person made and the ones teammates shared with their team (`?tab=shared`; else "Mine").
// The first page of the opening tab is read here; the other tab and older pages are asked for in the browser.
export default async function DocumentsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const api = await requireOwner();
  const raw = (await searchParams).tab;
  const tab: DocumentsTab = raw === "shared" ? "shared" : "mine";
  const initial = await load(api.documents.list({ ...(tab === "shared" ? { shared: true } : { mine: true }), limit: DOCUMENT_PAGE }));
  return (
    <AppShell
      current="documents"
      reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()}
      unread={await getUnreadNotifications()}
      user={await getShellUser()}
      teams={await getShellTeams()}
      timeZone={await getTimeZone()}
    >
      <DocumentsView key={tab} initialTab={tab} initial={initial} />
    </AppShell>
  );
}
