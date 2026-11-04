import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { cache } from "react";
import { AppShell } from "@/components/app/shell";
import { MeetingView } from "@/components/app/meeting-view";
import { getReviewOpen, getUnreadNotifications, load, requireOwner } from "@/lib/api/server";

type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ tab?: string | string[] }>;
};

// Ids are 1 to 10 digits; anything else is a 404 without asking FastAPI (which would say the same).
function parseId(raw: string): number {
  if (!/^\d{1,10}$/.test(raw)) notFound();
  return Number(raw);
}

// One fetch for the title and the page.
const fetchMeeting = cache(async (id: number) => {
  const api = await requireOwner();
  return load(api.meetings.get(id));
});

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const meeting = await fetchMeeting(parseId((await params).id));
  return { title: `Meeting ${meeting.native_id}`, robots: { index: false, follow: false } };
}

export default async function MeetingPage({ params, searchParams }: Props) {
  const id = parseId((await params).id);
  // The Review screen's "Open transcript" lands on the transcript tab.
  const { tab } = await searchParams;
  const api = await requireOwner();
  const [meeting, templates] = await Promise.all([fetchMeeting(id), load(api.templates.list())]);
  return (
    <AppShell current="meetings" reviewOpen={await getReviewOpen()} unread={await getUnreadNotifications()}>
      <MeetingView initial={meeting} templates={templates} initialTab={tab === "transcript" ? "transcript" : "notes"} />
    </AppShell>
  );
}
