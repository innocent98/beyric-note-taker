import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { TemplatesEditor } from "@/components/app/templates-editor";
import { getReviewOpen, getTasksNew, getShellUser, getUnreadNotifications, load, requireTeamOwner, getShellTeams } from "@/lib/api/server";

export const metadata: Metadata = { title: "Templates", robots: { index: false, follow: false } };

// Templates belong to the active workspace (`?team=`, else the default one): members read and use
// them, owners and admins write them. If the team cannot be read the editor is drawn as for the
// person's own workspace, and the server still refuses a write it should not allow.
export default async function TemplatesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { api } = await requireTeamOwner("/templates", await searchParams);
  const templates = await load(api.templates.list());
  const team = await api.team.get().catch(() => null);
  const workspace = team
    ? { name: team.is_personal ? "your personal workspace" : team.name, isPersonal: team.is_personal, canEdit: team.can_manage }
    : undefined;
  return (
    <AppShell current="templates" reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()} unread={await getUnreadNotifications()} user={await getShellUser()} teams={await getShellTeams()}>
      <TemplatesEditor initial={templates} workspace={workspace} />
    </AppShell>
  );
}
