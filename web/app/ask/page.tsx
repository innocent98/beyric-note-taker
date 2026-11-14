import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app/shell";
import { AskView } from "@/components/app/ask-view";
import { ApiError } from "@/lib/api/client";
import type { AskScope } from "@/lib/api/types";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, requireOwner, getShellTeams } from "@/lib/api/server";
import { initials } from "@/lib/format";

export const metadata: Metadata = { title: "Ask Beyric", robots: { index: false, follow: false } };

export default async function AskPage() {
  const api = await requireOwner();
  // A failed scope load is not an error page: the box still works, only the chips and the scope
  // line are left out. A 401 still goes to /login.
  let scope: AskScope | null = null;
  try {
    scope = await api.ask.scope();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
  }
  const user = await getShellUser();
  const name = user?.name?.trim() || user?.email.split("@")[0] || "";
  return (
    <AppShell
      current="ask"
      reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()}
      unread={await getUnreadNotifications()} user={user} teams={await getShellTeams()}
      timeZone={await getTimeZone()}
    >
      <AskView scope={scope} initials={name ? initials(name) : "?"} />
    </AppShell>
  );
}
