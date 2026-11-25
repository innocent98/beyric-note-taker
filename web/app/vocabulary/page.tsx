import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { VocabularyManager } from "@/components/app/vocabulary-manager";
import { getReviewOpen, getTasksNew, getShellUser, getUnreadNotifications, load, requireTeamOwner, getShellTeams } from "@/lib/api/server";

export const metadata: Metadata = { title: "Vocabulary", robots: { index: false, follow: false } };

// The vocabulary belongs to the active workspace (`?team=`, else the default one): members read it,
// owners and admins write it.
export default async function VocabularyPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { api } = await requireTeamOwner("/vocabulary", await searchParams);
  const terms = await load(api.vocabulary.list());
  const team = await api.team.get().catch(() => null);
  const workspace = team
    ? { name: team.is_personal ? "your personal workspace" : team.name, isPersonal: team.is_personal, canEdit: team.can_manage }
    : undefined;
  return (
    <AppShell current="vocabulary" reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()} unread={await getUnreadNotifications()} user={await getShellUser()} teams={await getShellTeams()}>
      <VocabularyManager initial={terms} workspace={workspace} />
    </AppShell>
  );
}
