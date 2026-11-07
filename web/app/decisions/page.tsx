import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app/shell";
import { DecisionsView } from "@/components/app/decisions-view";
import { ApiError } from "@/lib/api/client";
import type { DecisionList } from "@/lib/api/types";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, requireOwner } from "@/lib/api/server";

export const metadata: Metadata = { title: "Decisions", robots: { index: false, follow: false } };

export default async function DecisionsPage() {
  const api = await requireOwner();
  // A failed first load is the designer's "Could not load decisions" state (with Try again), not
  // the error page; a 401 still goes to /login.
  let initial: DecisionList | null = null;
  try {
    initial = await api.decisions.list();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
  }
  return (
    <AppShell
      current="decisions"
      reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()}
      unread={await getUnreadNotifications()} user={await getShellUser()}
      timeZone={await getTimeZone()}
    >
      <DecisionsView initial={initial} />
    </AppShell>
  );
}
