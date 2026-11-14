import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app/shell";
import { EmailSettingsSection } from "@/components/app/email-settings-section";
import { SettingsCalendar } from "@/components/app/settings-calendar";
import { SettingsBotName } from "@/components/app/settings-bot-name";
import { SettingsLanguage } from "@/components/app/settings-language";
import { SettingsTimeZone } from "@/components/app/settings-time-zone";
import { SettingsPlannedSections } from "@/components/app/settings-planned-sections";
import { PageHead } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { getReviewOpen, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, load, requireOwner } from "@/lib/api/server";
import { calendarNotice } from "@/lib/calendar-notice";
import type { CalendarState, Template } from "@/lib/api/types";

export const metadata: Metadata = { title: "Settings", robots: { index: false, follow: false } };

// The Settings screen. Each section is its own component; this file only frames them. The Calendar
// section is where Google's callback sends the browser back (`?calendar=connected` or
// `?calendar_error=<code>`). While the server has the calendar off, GET /api/calendar answers 404
// `calendar_unavailable` and the section says so, so that call is not wrapped in `load` (which
// turns every 404 into the not-found page). The designer's sections follow it, and the Email section (not in the design) comes last.
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
  // The select of the Calendar section lists the person's own templates. Without them the select is
  // left out; the rest of the page does not depend on them.
  let templates: Template[] = [];
  try {
    templates = await api.templates.list();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
  }
  // The Tasks card's two switches; if they cannot be read the card is drawn off, as before.
  const tasksSettings = await api.settings.tasks.get().catch(() => null);
  const slack = await api.integrations.slack.get().catch(() => null);
  // The Sprint length, Monday to Friday and reminders rows of the Sprints and reminders card; if the
  // team cannot be read they are drawn off. The sprints give the line under "Sprint length".
  const team = await api.team.get().catch(() => null);
  const sprints = team ? await api.sprints.list().catch(() => null) : null;
  const reminderMail = await api.settings.reminders.get().catch(() => null);
  const linear = await api.integrations.linear.get().catch(() => null);
  const [email, language, timeZone, botName] = await Promise.all([
    load(api.settings.email.get()),
    load(api.settings.language.get()),
    load(api.settings.timeZone.get()),
    load(api.settings.botName.get()),
  ]);
  return (
    <AppShell current="settings" reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()} unread={await getUnreadNotifications()} user={await getShellUser()} timeZone={await getTimeZone()}>
      <div className="stack-md measure">
        <PageHead title="Settings" subtitle="Preferences for your notes and transcripts." />
        <SettingsCalendar initial={calendar} notice={calendarNotice(await searchParams)} templates={templates} />
        <SettingsPlannedSections tasks={tasksSettings} slack={slack} linear={linear} team={team} reminderMail={reminderMail} sprints={sprints} />
        <SettingsTimeZone initial={timeZone.time_zone} />
        <SettingsBotName initial={botName} />
        <SettingsLanguage initial={language.default_language} />
        <EmailSettingsSection email={email} />
      </div>
    </AppShell>
  );
}
