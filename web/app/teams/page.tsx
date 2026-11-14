import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";
import { AppShell } from "@/components/app/shell";
import { TeamsView } from "@/components/app/teams-view";
import { ApiError } from "@/lib/api/client";
import type { TeamList } from "@/lib/api/types";
import { getMultiTeam, getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, getShellTeams, requireOwner } from "@/lib/api/server";

export const metadata: Metadata = { title: "Teams", robots: { index: false, follow: false } };

// The teams a person belongs to, and making a new one (multi-team stage 3, lead-designed: the designer
// drew no such screen). It exists only while the server's multi-team switch is on: with it off the page is
// not there, exactly as before.
export default async function TeamsPage() {
  const api = await requireOwner();
  if (!(await getMultiTeam())) notFound();
  // A failed first load is the page's "Could not load your teams" state with Try again; a 401 goes to /login.
  let initial: TeamList | null = null;
  try {
    initial = await api.teams.list();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
  }
  return (
    <AppShell
      current="teams"
      reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()}
      unread={await getUnreadNotifications()} user={await getShellUser()} teams={await getShellTeams()}
      timeZone={await getTimeZone()}
    >
      <TeamsView initial={initial} />
    </AppShell>
  );
}
