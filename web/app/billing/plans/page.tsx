import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { PlansView } from "@/components/billing/plans-view";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, requireOwner, getShellTeams } from "@/lib/api/server";

export const metadata: Metadata = { title: "Choose a plan", robots: { index: false, follow: false } };

// Plans and checkout (stage S8b part A).
export default async function PlansPage() {
  await requireOwner();
  return (
    <AppShell current="billing" reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()} unread={await getUnreadNotifications()} user={await getShellUser()} teams={await getShellTeams()} timeZone={await getTimeZone()}>
      <PlansView />
    </AppShell>
  );
}
