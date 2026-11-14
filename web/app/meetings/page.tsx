import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { MeetingsCalendar } from "@/components/app/meetings-calendar";
import { MeetingList } from "@/components/app/meeting-list";
import { NewMeetingForm } from "@/components/app/new-meeting-form";
import { RecurringCards } from "@/components/app/recurring-cards";
import { Alert, PageHead } from "@/components/ui";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, load, requireOwner, serverApi } from "@/lib/api/server";
import type { CalendarState, NotRecordedItem } from "@/lib/api/types";
import { isFiltered, readRange } from "@/lib/date-range";

// The owner's Meetings page: the home of the signed-in app. `/` is the landing page for everyone
// (app/page.tsx); this page is for the owner only (a visitor goes to /login) and is never indexed.
export const metadata: Metadata = { title: "Meetings", robots: { index: false, follow: false } };

// The calendar is optional on this page: while the server has it off (404 `calendar_unavailable`)
// or cannot answer, the page is drawn without it.
async function getCalendar(api: Awaited<ReturnType<typeof serverApi>>): Promise<CalendarState | null> {
  try {
    return await api.calendar.get();
  } catch {
    return null;
  }
}

// Not recorded is optional too: no list is drawn when it cannot be read.
async function getNotRecorded(api: Awaited<ReturnType<typeof serverApi>>): Promise<NotRecordedItem[]> {
  try {
    return (await api.calendar.notRecorded()).items;
  } catch {
    return [];
  }
}

// The date filter is in the address (`/meetings?from=2026-11-01&to=2026-11-07`, lead-designed), so a
// filtered view can be reloaded and shared. A damaged value is ignored with a line about it.
export default async function MeetingsPage({
  searchParams,
}: {
  searchParams?: Promise<Record<string, string | string[] | undefined>>;
} = {}) {
  const api = await requireOwner();
  const { range, notice } = readRange((await searchParams) ?? {});
  const [[list, templates], calendar, notRecorded] = await Promise.all([
    load(Promise.all([api.meetings.list({ from: range.from ?? undefined, to: range.to ?? undefined }), api.templates.list()])),
    getCalendar(api),
    getNotRecorded(api),
  ]);
  // "A bot is already on a meeting" is about the newest meetings, not about the filtered ones.
  const newest = isFiltered(range) ? await load(api.meetings.list({ limit: 10 })) : list;
  const busy = newest.meetings.some((m) => m.status === "bot_sent" || m.status === "processing");
  return (
    <AppShell current="meetings" reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()} unread={await getUnreadNotifications()} user={await getShellUser()} timeZone={await getTimeZone()}>
      {list.worker_stale ? (
        <Alert kind="notice" role="alert">
          The background worker has not checked in for over 20 minutes. New meetings will not be processed until
          it is running again.
        </Alert>
      ) : null}
      <div className="stack">
        <PageHead title="Meetings" subtitle="Send the note taker to a call and find your notes here afterwards." />
        <NewMeetingForm templates={templates} busy={busy} />
        <MeetingsCalendar state={calendar} notRecorded={notRecorded} />
        <RecurringCards />
        <MeetingList initial={list} templates={templates} range={range} rangeNotice={notice} />
      </div>
    </AppShell>
  );
}
