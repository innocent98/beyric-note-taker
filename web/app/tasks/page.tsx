import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app/shell";
import { TasksView } from "@/components/app/tasks-view";
import { ApiError } from "@/lib/api/client";
import type { TaskList } from "@/lib/api/types";
import { getReviewOpen, getTasksNew, getTimeZone, getUnreadNotifications, requireOwner } from "@/lib/api/server";

export const metadata: Metadata = { title: "Tasks", robots: { index: false, follow: false } };

export default async function TasksPage() {
  const api = await requireOwner();
  // A failed first load is the page's "Could not load tasks" state with Try again, not the error
  // page; a 401 still goes to /login.
  let initial: TaskList | null = null;
  try {
    initial = await api.tasks.list({ limit: 500 });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
  }
  return (
    <AppShell
      current="tasks"
      reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()}
      unread={await getUnreadNotifications()}
      timeZone={await getTimeZone()}
    >
      <TasksView initial={initial} />
    </AppShell>
  );
}
