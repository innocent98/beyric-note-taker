import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { NotificationsView } from "@/components/app/notifications-view";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, load, requireTeamOwner, getShellTeams } from "@/lib/api/server";

export const metadata: Metadata = { title: "Notifications", robots: { index: false, follow: false } };

// The bell lists the active workspace's notifications: `?team=`, else the default workspace.
export default async function NotificationsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const { api, teamId } = await requireTeamOwner("/notifications", await searchParams);
  const list = await load(api.notifications.list({ limit: 50 }));
  return (
    <AppShell current="notifications" reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()} unread={await getUnreadNotifications()} user={await getShellUser()} teams={await getShellTeams()} timeZone={await getTimeZone()}>
      <NotificationsView key={teamId ?? "default"} initial={list.notifications} />
    </AppShell>
  );
}
