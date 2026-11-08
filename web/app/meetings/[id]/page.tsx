import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { cache } from "react";
import { AppShell } from "@/components/app/shell";
import { parseLine } from "@/lib/ask";
import { MeetingView } from "@/components/app/meeting-view";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, load, requireOwner } from "@/lib/api/server";

type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ tab?: string | string[]; line?: string | string[] }>;
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
  // The Review screen's "Open transcript" lands on the transcript tab; Ask Beyric's source chips add
  // `line` (the row to scroll to and highlight).
  const { tab, line } = await searchParams;
  const api = await requireOwner();
  const [meeting, templates, language] = await Promise.all([
    fetchMeeting(id),
    load(api.templates.list()),
    load(api.settings.language.get()),
  ]);
  // Only to know whether there is a team to share with; a failure just hides the switch.
  const teamSize = meeting.can_share ? await api.team.get().then((team) => team.member_count, () => 0) : 0;
  // For "Share notes": the team's addresses (a participant that matches a member gets theirs) and
  // the shares so far. Either failing only leaves the dialog without them.
  const [members, shares] = meeting.can_share
    ? await Promise.all([
        api.team.members().then((r) => r.members, () => []),
        api.meetings.shares.list(id).catch(() => []),
      ])
    : [[], []];
  return (
    <AppShell
      current="meetings"
      reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()}
      unread={await getUnreadNotifications()} user={await getShellUser()}
      timeZone={await getTimeZone()}
    >
      <MeetingView
        initial={meeting}
        templates={templates}
        initialTab={tab === "transcript" ? "transcript" : "notes"}
        initialLine={parseLine(line)}
        defaultLanguage={language.default_language}
        teamSize={teamSize}
        members={members}
        initialShares={shares}
      />
    </AppShell>
  );
}
