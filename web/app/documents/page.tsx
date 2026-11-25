import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { DocumentsView, type DocumentsTab } from "@/components/app/documents-view";
import { DOCUMENT_PAGE } from "@/lib/documents";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, load, requireTeamOwner, getShellTeams } from "@/lib/api/server";

export const metadata: Metadata = { title: "Documents", robots: { index: false, follow: false } };

// The drafts the person made in the active workspace and the ones teammates shared with it (`?tab=shared`; else
// "Mine"). The workspace is `?team=` (else the default one): the first page of the opening tab is read here
// for it, and the other tab and older pages are asked for in the browser for the same one.
export default async function DocumentsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const params = await searchParams;
  const { api, teamId } = await requireTeamOwner("/documents", params);
  const raw = params.tab;
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
      {/* keyed by the workspace too: following a remembered workspace swaps the address without a reload, and the list kept in state must not outlive it */}
      <DocumentsView key={`${tab}-${teamId ?? "default"}`} initialTab={tab} initial={initial} />
    </AppShell>
  );
}
