import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app/shell";
import { TeamView } from "@/components/app/team-view";
import { ApiError } from "@/lib/api/client";
import type { Team, TeamMembers } from "@/lib/api/types";
import { getReviewOpen, getTasksNew, getTimeZone, getUnreadNotifications, requireOwner } from "@/lib/api/server";

export const metadata: Metadata = { title: "Team", robots: { index: false, follow: false } };

export default async function TeamPage() {
  const api = await requireOwner();
  // A failed first load is the page's "Could not load your team" state with Try again, not the
  // error page; a 401 still goes to /login.
  let team: Team | null = null;
  let members: TeamMembers | null = null;
  try {
    [team, members] = await Promise.all([api.team.get(), api.team.members()]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
  }
  return (
    <AppShell
      current="team"
      reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()}
      unread={await getUnreadNotifications()}
      timeZone={await getTimeZone()}
    >
      <TeamView initialTeam={team} initialMembers={members} />
    </AppShell>
  );
}
