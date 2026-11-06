import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app/shell";
import { ProjectsView } from "@/components/app/projects-view";
import { ApiError } from "@/lib/api/client";
import type { ProjectList } from "@/lib/api/types";
import { getReviewOpen, getTasksNew, getTimeZone, getUnreadNotifications, requireOwner } from "@/lib/api/server";

export const metadata: Metadata = { title: "Projects", robots: { index: false, follow: false } };

export default async function ProjectsPage() {
  const api = await requireOwner();
  // A failed first load is the page's "Could not load projects" state with Try again; a 401 still
  // goes to /login.
  let initial: ProjectList | null = null;
  try {
    initial = await api.projects.list();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
  }
  return (
    <AppShell
      current="projects"
      reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()}
      unread={await getUnreadNotifications()}
      timeZone={await getTimeZone()}
    >
      <ProjectsView initial={initial} />
    </AppShell>
  );
}
