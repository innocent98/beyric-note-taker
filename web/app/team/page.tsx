import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app/shell";
import { TeamView } from "@/components/app/team-view";
import { ApiError } from "@/lib/api/client";
import type { Team, TeamMembers } from "@/lib/api/types";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, requireTeamOwner, getShellTeams } from "@/lib/api/server";

export const metadata: Metadata = { title: "Team", robots: { index: false, follow: false } };

export default async function TeamPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const { api, teams } = await requireTeamOwner("/team", await searchParams);
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
      unread={await getUnreadNotifications()} user={await getShellUser()} teams={await getShellTeams()}
      timeZone={await getTimeZone()}
    >
      <TeamView initialTeam={team} initialMembers={members} multiTeam={teams !== null} />
    </AppShell>
  );
}
