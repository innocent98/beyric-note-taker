import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { TemplatesEditor } from "@/components/app/templates-editor";
import { getReviewOpen, getTasksNew, getShellUser, getUnreadNotifications, load, requireOwner, getShellTeams } from "@/lib/api/server";

export const metadata: Metadata = { title: "Templates", robots: { index: false, follow: false } };

export default async function TemplatesPage() {
  const api = await requireOwner();
  const templates = await load(api.templates.list());
  return (
    <AppShell current="templates" reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()} unread={await getUnreadNotifications()} user={await getShellUser()} teams={await getShellTeams()}>
      <TemplatesEditor initial={templates} />
    </AppShell>
  );
}
