import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app/shell";
import { ProjectsView } from "@/components/app/projects-view";
import { ApiError } from "@/lib/api/client";
import type { ProjectList, SeriesList } from "@/lib/api/types";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, requireOwner } from "@/lib/api/server";

export const metadata: Metadata = { title: "Projects", robots: { index: false, follow: false } };

export default async function ProjectsPage() {
  const api = await requireOwner();
  // A failed first load (of the projects or of the recurring meetings the checklist lists) is the page's
  // "Could not load projects" state with Try again; a 401 still goes to /login.
  let initial: ProjectList | null = null;
  let series: SeriesList | null = null;
  try {
    [initial, series] = await Promise.all([api.projects.list(), api.series.list()]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
  }
  // "Workspace default (...)" in the Sprint length select; without it the select says "Workspace default".
  const team = await api.team.get().catch(() => null);
  return (
    <AppShell
      current="projects"
      reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()}
      unread={await getUnreadNotifications()} user={await getShellUser()}
      timeZone={await getTimeZone()}
    >
      <ProjectsView initial={initial} series={series} team={team} />
    </AppShell>
  );
}
