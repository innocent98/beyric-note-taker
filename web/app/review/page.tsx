import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app/shell";
import { ReviewView } from "@/components/app/review-view";
import { ApiError } from "@/lib/api/client";
import type { ReviewList } from "@/lib/api/types";
import { getReviewOpen, getTasksNew, getTimeZone, getUnreadNotifications, requireOwner } from "@/lib/api/server";

export const metadata: Metadata = { title: "Review", robots: { index: false, follow: false } };

export default async function ReviewPage() {
  const api = await requireOwner();
  // A failed first load is the designer's "Could not load review items" state (with Try again), not
  // the error page; a 401 still goes to /login.
  let lists: { open: ReviewList; checked: ReviewList } | null = null;
  try {
    const [open, checked] = await Promise.all([api.review.list("open"), api.review.list("checked")]);
    lists = { open, checked };
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
  }
  return (
    <AppShell
      current="review"
      reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()}
      unread={await getUnreadNotifications()}
      timeZone={await getTimeZone()}
    >
      <ReviewView open={lists?.open ?? null} checked={lists?.checked ?? null} />
    </AppShell>
  );
}
