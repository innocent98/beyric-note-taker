import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { ReviewView } from "@/components/app/review-view";
import { getReviewOpen, load, requireOwner } from "@/lib/api/server";

export const metadata: Metadata = { title: "Review", robots: { index: false, follow: false } };

export default async function ReviewPage() {
  const api = await requireOwner();
  const [open, checked] = await load(Promise.all([api.review.list("open"), api.review.list("checked")]));
  return (
    <AppShell current="review" reviewOpen={await getReviewOpen()}>
      <ReviewView open={open} checked={checked} />
    </AppShell>
  );
}
