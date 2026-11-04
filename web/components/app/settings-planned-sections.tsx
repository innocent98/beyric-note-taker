import type { ReactNode } from "react";

// The Settings sections the designer drew below Calendar (`isSettings` in Beyric Note Taker.dc.html):
// Sprints and reminders, Tasks, Default notes language. Their features are not built yet (tasks,
// sprints, reminders, translation, tool sync, Slack), so every control here is drawn as the designer
// drew it and switched off: a real `disabled` on each button and select (the switches carry
// `aria-disabled` too), no state, no network, nothing stored. Each section says so in one line;
// those three lines are the only words the design does not have. Sample values (the select
// choices) are the designer's own; no sprint dates and no connected state are shown, because there
// is no data behind them. Plain server markup on purpose: nothing here may ever call anything.

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
const LANGUAGES = [
  ["en", "Original (no translation)"],
  ["fr", "French"],
  ["es", "Spanish"],
] as const;

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

function Globe() {
  return (
    <svg
      className="pl-globe"
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
    </svg>
  );
}

export function SettingsPlannedSections() {
  return (
    <>
      <section className="card card-flush" aria-labelledby="set-sprints">
        <div className="pl-head">
          <div>
            <h2 id="set-sprints" className="set-title">
              Sprints and reminders
            </h2>
            <p className="pl-sub">Workspace defaults. Projects can use their own sprint length.</p>
            <p className="meta pl-soon">These settings are not available yet.</p>
          </div>
          <span className="pl-pill">Owners and admins</span>
        </div>
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
      </section>

      <section className="card card-flush" aria-labelledby="set-tasks">
        <div className="pl-head pl-head-plain">
          <div>
            <h2 id="set-tasks" className="set-title">
              Tasks
            </h2>
            <p className="pl-sub">What happens to action items after a meeting ends.</p>
            <p className="meta pl-soon">These settings are not available yet.</p>
          </div>
        </div>
        <div className="pl-row">
          <Text title="Create tasks from action items">
            Each action item becomes a task assigned to the teammate named as its owner.
          </Text>
          <Switch label="Create tasks from action items" />
        </div>
        <div className="pl-row">
          <Text title="Email assignees">Send each person a list of their new tasks after the meeting.</Text>
          <Switch label="Email assignees" />
        </div>
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
        <div className="pl-row pl-row-center pl-row-last">
          <Text title="Post tasks to Slack">Share new tasks in a channel so the team can track them there.</Text>
          <button type="button" className="btn btn-secondary pl-btn" disabled>
            Connect Slack
          </button>
        </div>
      </section>

      <section className="card pl-language" aria-labelledby="set-language">
        <div className="pl-language-text">
          <h2 id="set-language" className="set-title">
            Default notes language
          </h2>
          <p className="set-sub">
            Notes and transcripts open in this language. You can switch back to the original on any meeting.
          </p>
          <p className="meta pl-soon">This setting is not available yet.</p>
        </div>
        <div className="pl-language-control">
          <div className="pl-language-select">
            <Globe />
            <select className="select pl-lang" aria-label="Default notes language" defaultValue="en" disabled>
              {LANGUAGES.map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </select>
          </div>
        </div>
      </section>
    </>
  );
}
