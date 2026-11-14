import type { ReactNode } from "react";
import { SettingsSlack } from "@/components/app/settings-slack";
import { SettingsSprintRows } from "@/components/app/settings-sprints";
import { SettingsTracker } from "@/components/app/settings-tracker";
import { SettingsTasksRows } from "@/components/app/settings-tasks";
import { SettingsRemindersRow } from "@/components/app/settings-reminders";
import { SettingsReminderMail } from "@/components/app/settings-reminder-mail";
import type {
  LinearState,
  ReminderPreferences,
  SlackState,
  SprintList,
  TasksSettings,
  Team,
} from "@/lib/api/types";

// The Settings sections the designer drew below Calendar (`isSettings` in Beyric Note Taker.dc.html):
// Sprints and reminders, Tasks. Their features are not built yet (tasks, sprints, reminders, tool
// sync, Slack), so every control here is drawn as the designer drew it and switched off: a real `disabled` on each button and select (the switches carry
// `aria-disabled` too), no state, no network, nothing stored. Each section says so in one line;
// those two lines are the only words the design does not have. Sample values (the select
// choices) are the designer's own; no sprint dates and no connected state are shown, because there
// is no data behind them. Plain server markup on purpose: nothing here may ever call anything.
// Post tasks to Slack is live when `slack` is given (settings-slack.tsx); Sync with your project tool stays off (W10, tracker).
// The third section the designer drew there, Default notes language, is live: `settings-language.tsx`.

const SPRINT_LENGTHS = [
  ["week5", "1 week, 5 working days"],
  ["week7", "1 week, 7 days"],
  ["week14", "2 weeks"],
  ["month1", "1 month, 30 days"],
  ["month3", "3 months"],
  ["month6", "6 months"],
  ["custom", "Custom"],
] as const;
const REMINDER_TIMINGS = ["1 hour before", "30 minutes before", "1 hour and 30 minutes before"] as const;
const PROJECT_TOOLS = ["Jira", "Linear", "Asana", "Taiga"] as const;

// A switch that is off and cannot be turned on.
function Switch({ label }: { label: string }) {
  return (
    <button type="button" role="switch" aria-checked="false" aria-disabled="true" aria-label={label} className="pl-switch" disabled>
      <span className="pl-knob" />
    </button>
  );
}

function Select({
  label,
  size,
  defaultValue,
  options,
}: {
  label: string;
  size: "pl-s36" | "pl-s34";
  defaultValue: string;
  options: ReadonlyArray<readonly [string, string]>;
}) {
  return (
    <select className={`select ${size}`} aria-label={label} defaultValue={defaultValue} disabled>
      {options.map(([value, text]) => (
        <option key={value} value={value}>
          {text}
        </option>
      ))}
    </select>
  );
}

function Text({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div>
      <div className="pl-row-title">{title}</div>
      {children ? <div className="pl-row-sub">{children}</div> : null}
    </div>
  );
}

// `tasks` makes the first two rows of the Tasks card live (Create tasks, Email assignees); without it
// (the settings could not be read) they are drawn off like the rest.
// `team` makes the Sprint length and Monday to Friday rows (W9, with `sprints` giving the line under
// "Sprint length") and the reminders row (W8) live (owners and admins change them); without it (the
// team could not be read) the rows are drawn off.
export function SettingsPlannedSections({
  tasks = null,
  slack = null,
  linear = null,
  team = null,
  reminderMail = null,
  sprints = null,
  integrations = true,
}: {
  tasks?: TasksSettings | null;
  slack?: SlackState | null;
  linear?: LinearState | null;
  team?: Team | null;
  reminderMail?: ReminderPreferences | null;
  sprints?: SprintList | null;
  // false for a personal space while multi-team is on: Slack and Linear belong to a team, so nothing can be
  // connected there (the server refuses it: 409 `personal_team`). A row is then drawn only for an integration
  // that is ALREADY connected, with Disconnect and a line saying why it cannot be added again.
  integrations?: boolean;
}) {
  // The Tasks card's rows that are still drawn off, named in its one "not available yet" line.
  const pending = integrations
    ? [...(linear ? [] : ["Sync with your project tool"]), ...(slack ? [] : ["Post tasks to Slack"])]
    : [];
  return (
    <>
      <section className="card card-flush" aria-labelledby="set-sprints">
        <div className="pl-head">
          <div>
            <h2 id="set-sprints" className="set-title">
              Sprints and reminders
            </h2>
            <p className="pl-sub">Workspace defaults. Projects can use their own sprint length.</p>
            {team ? null : <p className="meta pl-soon">These settings are not available yet.</p>}
            {team && !team.can_manage ? (
              <p className="meta pl-soon">Only an owner or admin of your team can change the sprint settings.</p>
            ) : null}
          </div>
          <span className="pl-pill">Owners and admins</span>
        </div>
        {team ? (
          <SettingsSprintRows team={team} list={sprints} />
        ) : (
          <>
            <div className="pl-row pl-row-center">
              <Text title="Sprint length" />
              <div className="pl-controls">
                <Select label="Sprint length" size="pl-s36" defaultValue="week5" options={SPRINT_LENGTHS} />
              </div>
            </div>
            <div className="pl-row">
              <Text title="Open and close sprints at recurring meetings">Available for 1 week, 5 working day sprints.</Text>
              <Switch label="Open and close sprints at recurring meetings" />
            </div>
          </>
        )}
        {team ? (
          <SettingsRemindersRow team={team} />
        ) : (
        <div className="pl-row pl-row-remind">
            <div className="pl-grow">
              <Text title="Remind attendees before recurring meetings">
                The email covers what the last meeting focused on, decisions made, open questions and unfinished tasks.
              </Text>
            </div>
            <div className="pl-controls pl-controls-wide">
              <Select
                label="Reminder timing"
                size="pl-s34"
                defaultValue="1 hour and 30 minutes before"
                options={REMINDER_TIMINGS.map((timing) => [timing, timing] as const)}
              />
              <Switch label="Remind attendees before recurring meetings" />
            </div>
          </div>
        )}
        {reminderMail ? <SettingsReminderMail initial={reminderMail} /> : null}
      </section>

      <section className="card card-flush" aria-labelledby="set-tasks">
        <div className="pl-head pl-head-plain">
          <div>
            <h2 id="set-tasks" className="set-title">
              Tasks
            </h2>
            <p className="pl-sub">What happens to action items after a meeting ends.</p>
            {pending.length > 0 ? (
              <p className="meta pl-soon">{tasks ? `${pending.join(" and ")} ${pending.length > 1 ? "are" : "is"} not available yet.` : "These settings are not available yet."}</p>
            ) : null}
          </div>
        </div>
        {tasks ? (
          <SettingsTasksRows initial={tasks} />
        ) : (
          <>
        <div className="pl-row">
          <Text title="Create tasks from action items">
            Each action item becomes a task assigned to the teammate named as its owner.
          </Text>
          <Switch label="Create tasks from action items" />
        </div>
        <div className="pl-row">
          <Text title="Ask before adding unclear tasks">
            Clear commitments like &quot;I&apos;ll take checkout&quot; are added straight away. Vague or conditional ones wait
            under Suggested tasks.
          </Text>
          <Switch label="Ask before adding unclear tasks" />
        </div>
        <div className="pl-row">
          <Text title="Email assignees">Send each person a list of their new tasks after the meeting.</Text>
          <Switch label="Email assignees" />
        </div>
          </>
        )}
        {!integrations ? (
          linear?.connected ? <SettingsTracker initial={linear} personal /> : null
        ) : linear ? (
          <SettingsTracker initial={linear} />
        ) : (
          <div className="pl-row pl-row-center">
            <Text title="Sync with your project tool">Send tasks, projects and sprints to a tool your team already uses.</Text>
            <div className="pl-controls">
              <Select
                label="Project tool"
                size="pl-s34"
                defaultValue="Jira"
                options={PROJECT_TOOLS.map((tool) => [tool, tool] as const)}
              />
              <button type="button" className="btn btn-secondary pl-btn" disabled>
                Connect
              </button>
            </div>
          </div>
        )}
        {!integrations ? (
          slack?.connected ? <SettingsSlack initial={slack} personal /> : null
        ) : slack ? (
          <SettingsSlack initial={slack} />
        ) : (
          <div className="pl-row pl-row-center pl-row-last">
            <Text title="Post tasks to Slack">Share new tasks in a channel so the team can track them there.</Text>
            <button type="button" className="btn btn-secondary pl-btn" disabled>
              Connect Slack
            </button>
          </div>
        )}
      </section>
    </>
  );
}
