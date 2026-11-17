import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { BillingView } from "@/components/billing/billing-view";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, requireOwner, getShellTeams } from "@/lib/api/server";

export const metadata: Metadata = { title: "Billing", robots: { index: false, follow: false } };

// Billing page (stage S8b). The data is read in the browser, because a 404 from the billing routes means
// "not available to this person" and is a plain message here, not the not-found page.
export default async function BillingPage() {
  await requireOwner();
  return (
    <AppShell current="settings" reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()} unread={await getUnreadNotifications()} user={await getShellUser()} teams={await getShellTeams()} timeZone={await getTimeZone()}>
      <BillingView />
    </AppShell>
  );
}
