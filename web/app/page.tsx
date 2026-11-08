import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { MeetingsCalendar } from "@/components/app/meetings-calendar";
import { MeetingList } from "@/components/app/meeting-list";
import { NewMeetingForm } from "@/components/app/new-meeting-form";
import { RecurringCards } from "@/components/app/recurring-cards";
import { Landing, LandingFooter, LandingHeader } from "@/components/landing/landing";
import { JsonLd, organizationJsonLd, softwareJsonLd } from "@/components/site/json-ld";
import { Alert, PageHead } from "@/components/ui";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, getSession, load, serverApi } from "@/lib/api/server";
import type { CalendarState, NotRecordedItem } from "@/lib/api/types";

// `/` is two pages: the landing page for a visitor who is not logged in (indexed, in the sitemap),
// and the owner's Meetings page for the owner (never indexed). GET /api/session says which.
export async function generateMetadata(): Promise<Metadata> {
  if ((await getSession())?.authenticated) return { title: "Meetings", robots: { index: false, follow: false } };
  return {
    title: { absolute: "Beyric Note Taker - clear notes from every meeting" },
    alternates: { canonical: "/" },
  };
}

export default async function HomePage() {
  if (!(await getSession())?.authenticated) return <LandingPage />;
  return <MeetingsPage />;
}

function LandingPage() {
  return (
    <div className="ld">
      <JsonLd data={[organizationJsonLd(), softwareJsonLd()]} />
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <LandingHeader />
      <main id="main">
        <Landing />
      </main>
      <LandingFooter />
    </div>
  );
}

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

async function MeetingsPage() {
  const api = await serverApi();
  const [[list, templates], calendar, notRecorded] = await Promise.all([
    load(Promise.all([api.meetings.list(), api.templates.list()])),
    getCalendar(api),
    getNotRecorded(api),
  ]);
  const busy = list.meetings.some((m) => m.status === "bot_sent" || m.status === "processing");
  return (
    <AppShell current="meetings" reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()} unread={await getUnreadNotifications()} user={await getShellUser()} timeZone={await getTimeZone()}>
      {list.worker_stale ? (
        <Alert kind="notice" role="alert">
          The background worker has not checked in for over 20 minutes. New meetings will not be processed until it
          is running again.
        </Alert>
      ) : null}
      <div className="stack">
        <PageHead title="Meetings" subtitle="Send the note taker to a call and find your notes here afterwards." />
        <NewMeetingForm templates={templates} busy={busy} />
        <MeetingsCalendar state={calendar} notRecorded={notRecorded} />
        <RecurringCards />
        <MeetingList initial={list} templates={templates} />
      </div>
    </AppShell>
  );
}
