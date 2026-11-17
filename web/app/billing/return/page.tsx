import type { Metadata } from "next";
import { Suspense } from "react";
import { AppShell } from "@/components/app/shell";
import { ReturnView } from "@/components/billing/return-view";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, requireOwner, getShellTeams } from "@/lib/api/server";

export const metadata: Metadata = { title: "Your payment", robots: { index: false, follow: false } };

// Where the payment provider sends the person back to (`PUBLIC_BASE_URL/billing/return`, or
// `BILLING_RETURN_URL`). The address names the payment; the page reads its state from us and polls.
// Not signed in: the visitor goes to /login (the payment is theirs to see only when they are).
export default async function BillingReturnPage() {
  await requireOwner();
  return (
    <AppShell current="billing" reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()} unread={await getUnreadNotifications()} user={await getShellUser()} teams={await getShellTeams()} timeZone={await getTimeZone()}>
      <Suspense fallback={null}>
        <ReturnView />
      </Suspense>
    </AppShell>
  );
}
