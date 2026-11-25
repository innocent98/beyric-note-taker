import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app/shell";
import { EmailSettingsSection } from "@/components/app/email-settings-section";
import { SettingsAutoShare } from "@/components/app/settings-auto-share";
import { SettingsCalendar } from "@/components/app/settings-calendar";
import { SettingsYourName } from "@/components/app/settings-your-name";
import { SettingsBotName } from "@/components/app/settings-bot-name";
import { SettingsWorkspaceBotName } from "@/components/app/settings-workspace-bot-name";
import { SettingsLanguage } from "@/components/app/settings-language";
import { SettingsTimeZone } from "@/components/app/settings-time-zone";
import { SettingsPlannedSections } from "@/components/app/settings-planned-sections";
import { SettingsRemovedRecurring } from "@/components/app/settings-removed-recurring";
import { SettingsBilling } from "@/components/billing/settings-billing";
import { SettingsBillingAdmin } from "@/components/billing/settings-billing-admin";
import { PageHead } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { getReviewOpen, getSession, getTasksNew, getTimeZone, getShellUser, getUnreadNotifications, load, requireTeamOwner, getShellTeams } from "@/lib/api/server";
import { calendarNotice } from "@/lib/calendar-notice";
import type { CalendarState, Template } from "@/lib/api/types";
import type { ReactNode } from "react";

export const metadata: Metadata = { title: "Settings", robots: { index: false, follow: false } };

// Two groups, lead-designed (the designer's file draws one flat list): "This workspace" (what changes
// with the workspace switcher: the calendar and its rule, the note taker name there, sprints,
// reminders, integrations) and "Personal" (what never switches: time zone, name, your default note
// taker name, language, who your meetings are shared to, billing, email). Each group is a heading
// and one line in the existing card-title style over the existing cards; nothing else is new.
function SettingsGroup({ id, title, sub, children }: { id: string; title: string; sub: string; children: ReactNode }) {
  return (
    <section className="stack-md" aria-labelledby={id}>
      <div>
        <h2 id={id} className="set-title">
          {title}
        </h2>
        <p className="set-sub">{sub}</p>
      </div>
      {children}
    </section>
  );
}

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
  const params = await searchParams;
  const { api, teams } = await requireTeamOwner("/settings", params);
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
  // Slack and Linear belong to a team: a personal space has none, and the server refuses them there.
  // Per person, only while the switch is on: the team this person's new meetings are shared to (null: none).
  const autoShare = teams ? await api.settings.autoShare.get().catch(() => null) : null;
  const noIntegrations = teams !== null && team?.is_personal === true;
  const session = await getSession();
  // The person's own note taker name in this workspace (null when it cannot be read: the card is left out).
  const workspacePrefs = await api.workspacePrefs.get().catch(() => null);
  const workspaceName = team ? (team.is_personal ? "your personal workspace" : team.name) : "this workspace";
  const [email, language, timeZone, botName, yourName] = await Promise.all([
    load(api.settings.email.get()),
    load(api.settings.language.get()),
    load(api.settings.timeZone.get()),
    load(api.settings.botName.get()),
    load(api.settings.name.get()),
  ]);
  return (
    <AppShell current="settings" reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()} unread={await getUnreadNotifications()} user={await getShellUser()} teams={await getShellTeams()} timeZone={await getTimeZone()}>
      <div className="stack-md measure">
        <PageHead title="Settings" subtitle="Preferences for your notes and transcripts." />
        <SettingsGroup
          id="settings-workspace"
          title="This workspace"
          sub={`Settings for ${workspaceName}. They change when you switch workspace.`}
        >
          <SettingsCalendar initial={calendar} notice={calendarNotice(params)} templates={templates} />
          {workspacePrefs ? <SettingsWorkspaceBotName initial={workspacePrefs} workspaceName={workspaceName} /> : null}
          <SettingsPlannedSections tasks={tasksSettings} slack={slack} linear={linear} integrations={!noIntegrations} team={team} reminderMail={reminderMail} sprints={sprints} />
          {team?.can_manage ? <SettingsRemovedRecurring /> : null}
        </SettingsGroup>
        <SettingsGroup id="settings-personal" title="Personal" sub="These follow you into every workspace.">
          {teams && autoShare ? <SettingsAutoShare initial={autoShare.team_id} teams={teams.teams} /> : null}
          <SettingsTimeZone initial={timeZone.time_zone} />
          <SettingsYourName initial={yourName} />
          <SettingsBotName initial={botName} />
          <SettingsLanguage initial={language.default_language} />
          <SettingsBilling />
          {session?.user?.is_admin ? <SettingsBillingAdmin /> : null}
          <EmailSettingsSection email={email} />
        </SettingsGroup>
      </div>
    </AppShell>
  );
}
