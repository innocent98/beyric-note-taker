import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AppShell } from "@/components/app/shell";
import { AdminBillingView } from "@/components/billing/admin-billing-view";
import {
  getReviewOpen,
  getSession,
  getShellTeams,
  getShellUser,
  getTasksNew,
  getTimeZone,
  getUnreadNotifications,
  requireOwner,
} from "@/lib/api/server";

export const metadata: Metadata = { title: "Refunds", robots: { index: false, follow: false } };

// The administrator's refund screen (billing stage S9). LEAD-DESIGNED, with the billing page's lists: no admin
// design exists. A person who is not an administrator gets the not-found page, as for any page that is not
// theirs; the API refuses them anyway (403), this only keeps the page from showing to them.
export default async function AdminBillingPage() {
  await requireOwner();
  if (!(await getSession())?.user?.is_admin) notFound();
  return (
    <AppShell current="settings" reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()} unread={await getUnreadNotifications()} user={await getShellUser()} teams={await getShellTeams()} timeZone={await getTimeZone()}>
      <AdminBillingView />
    </AppShell>
  );
}
