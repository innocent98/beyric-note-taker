import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { NotificationsView } from "@/components/app/notifications-view";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, load, requireOwner, getShellTeams } from "@/lib/api/server";

export const metadata: Metadata = { title: "Notifications", robots: { index: false, follow: false } };

export default async function NotificationsPage() {
  const api = await requireOwner();
  const list = await load(api.notifications.list({ limit: 50 }));
  return (
    <AppShell current="notifications" reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()} unread={await getUnreadNotifications()} user={await getShellUser()} teams={await getShellTeams()} timeZone={await getTimeZone()}>
      <NotificationsView initial={list.notifications} />
    </AppShell>
  );
}
