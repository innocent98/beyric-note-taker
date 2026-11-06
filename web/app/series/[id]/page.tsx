import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { cache } from "react";
import { AppShell } from "@/components/app/shell";
import { SeriesView } from "@/components/app/series-view";
import { getReviewOpen, getTasksNew, getTimeZone, getUnreadNotifications, load, requireOwner } from "@/lib/api/server";

type Props = { params: Promise<{ id: string }> };

// Ids are 1 to 10 digits; anything else is a 404 without asking FastAPI (which would say the same).
function parseId(raw: string): number {
  if (!/^\d{1,10}$/.test(raw)) notFound();
  return Number(raw);
}

// One fetch for the title and the page.
const fetchSeries = cache(async (id: number) => {
  const api = await requireOwner();
  return load(api.series.get(id));
});

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const series = await fetchSeries(parseId((await params).id));
  return { title: series.title, robots: { index: false, follow: false } };
}

export default async function SeriesPage({ params }: Props) {
  const series = await fetchSeries(parseId((await params).id));
  return (
    <AppShell
      current="meetings"
      reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()}
      unread={await getUnreadNotifications()}
      timeZone={await getTimeZone()}
    >
      <SeriesView initial={series} />
    </AppShell>
  );
}
