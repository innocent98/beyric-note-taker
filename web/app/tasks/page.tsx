import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app/shell";
import { TasksView } from "@/components/app/tasks-view";
import { ApiError } from "@/lib/api/client";
import type { TaskList } from "@/lib/api/types";
import { loadSprintBook } from "@/lib/sprints";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, requireTeamOwner, getShellTeams } from "@/lib/api/server";

export const metadata: Metadata = { title: "Tasks", robots: { index: false, follow: false } };

export default async function TasksPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const { api } = await requireTeamOwner("/tasks", await searchParams);
  // A failed first load is the page's "Could not load tasks" state with Try again, not the error
  // page; a 401 still goes to /login.
  let initial: TaskList | null = null;
  try {
    initial = await api.tasks.list({ limit: 500 });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
  }
  // The suggested tasks (the board leaves them out): a failure draws no section, the board still works.
  const suggested = await api.tasks.list({ suggested: true, limit: 500 }).then((list) => list.tasks, () => null);
  // The project tabs and milestone card: a failure leaves them out, the board still works.
  const projects = await api.projects.list().then((list) => list.projects, () => null);
  const milestones = await api.milestones.list().then((list) => list.milestones, () => []);
  // The sprint selector, the sprint card and the Backlog: a failure is drawn as a line with Try again.
  const sprints = await loadSprintBook(api, projects);
  // Only to know whether to offer "Invite them to the team": a failure here changes nothing else.
  const canInvite = await api.team.get().then((team) => team.can_manage, () => false);
  return (
    <AppShell
      current="tasks"
      reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()}
      unread={await getUnreadNotifications()} user={await getShellUser()} teams={await getShellTeams()}
      timeZone={await getTimeZone()}
    >
      <TasksView initial={initial} suggested={suggested} projects={projects} milestones={milestones} sprints={sprints} canInviteToTeam={canInvite} />
    </AppShell>
  );
}
