import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app/shell";
import { EmailSettingsSection } from "@/components/app/email-settings-section";
import { SettingsCalendar } from "@/components/app/settings-calendar";
import { SettingsLanguage } from "@/components/app/settings-language";
import { SettingsPlannedSections } from "@/components/app/settings-planned-sections";
import { PageHead } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { getReviewOpen, getUnreadNotifications, load, requireOwner } from "@/lib/api/server";
import { calendarNotice } from "@/lib/calendar-notice";
import type { CalendarState } from "@/lib/api/types";

export const metadata: Metadata = { title: "Settings", robots: { index: false, follow: false } };

// The Settings screen. Each section is its own component; this file only frames them. The Calendar
// section is where Google's callback sends the browser back (`?calendar=connected` or
// `?calendar_error=<code>`). While the server has the calendar off, GET /api/calendar answers 404
// `calendar_unavailable` and the section says so, so that call is not wrapped in `load` (which
// turns every 404 into the not-found page). The Email section is below it.
export default async function SettingsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const api = await requireOwner();
  let calendar: CalendarState | null;
  try {
    calendar = await api.calendar.get();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
    if (error instanceof ApiError && error.code === "calendar_unavailable") calendar = null;
    else throw error;
  }
  const [email, language] = await Promise.all([load(api.settings.email.get()), load(api.settings.language.get())]);
  return (
    <AppShell current="settings" reviewOpen={await getReviewOpen()} unread={await getUnreadNotifications()}>
      <div className="stack-md measure">
        <PageHead title="Settings" subtitle="Preferences for your notes and transcripts." />
        <SettingsCalendar initial={calendar} notice={calendarNotice(await searchParams)} />
        <SettingsPlannedSections />
        <SettingsLanguage initial={language.default_language} />
        <EmailSettingsSection email={email} />
      </div>
    </AppShell>
  );
}
