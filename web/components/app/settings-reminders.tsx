"use client";

import { useState } from "react";
import { Alert, Toggle } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { Team } from "@/lib/api/types";

// The last row of Settings > Sprints and reminders, live: "Remind attendees before recurring
// meetings" with its timing select (PATCH /api/team `reminders_on` and `reminder_timing`,
// docs/fe-integration-guide-series.md). Owners and admins change it; anyone else sees the team's
// real setting with the controls switched off (`can_manage`). Off until a manager switches it on.
// A failed save leaves the control where it was and says so. The three timings are the designer's.
const TIMINGS = [
  ["1h", "1 hour before"],
  ["30m", "30 minutes before"],
  ["both", "1 hour and 30 minutes before"],
] as const;

export function SettingsRemindersRow({ team }: { team: Team }) {
  const [value, setValue] = useState(team);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const locked = !value.can_manage || saving;

  async function save(changes: { reminders_on?: boolean; reminder_timing?: Team["reminder_timing"] }) {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      setValue(await api.team.update(changes));
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setSaving(false);
  }

  return (
    <>
      <div className="pl-row pl-row-remind">
        <div className="pl-grow">
          <div>
            <div className="pl-row-title" id="remind-label">
              Remind attendees before recurring meetings
            </div>
            <div className="pl-row-sub">
              The email covers what the last meeting focused on, decisions made, open questions and unfinished tasks.
            </div>
          </div>
        </div>
        <div className="pl-controls pl-controls-wide">
          {value.reminders_on ? (
            <select
              className="select pl-s34"
              aria-label="Reminder timing"
              value={value.reminder_timing}
              disabled={locked}
              onChange={(event) => void save({ reminder_timing: event.target.value as Team["reminder_timing"] })}
            >
              {TIMINGS.map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          ) : null}
          <Toggle
            checked={value.reminders_on}
            disabled={locked}
            aria-labelledby="remind-label"
            onClick={() => void save({ reminders_on: !value.reminders_on })}
          />
        </div>
      </div>
      <div role="status" aria-live="polite">
        {error ? (
          <div className="set-note">
            <Alert kind="error">{error}</Alert>
          </div>
        ) : null}
      </div>
    </>
  );
}
