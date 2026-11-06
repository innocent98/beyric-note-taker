"use client";

import { useState } from "react";
import { Alert, Toast, Toggle } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { useToday } from "@/components/app/use-today";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { SprintLength, SprintList, Team, TeamChanges } from "@/lib/api/types";
import { SPRINT_LENGTHS, sprintRange } from "@/lib/sprints";

// The first two rows of Settings > Sprints and reminders, live (the designer's `sprintLen`,
// `customDays`, `sprintPreview`, `alignTg` and `alignDesc`): the Sprint length select with, under its
// title, the current sprint and its dates; the number of days beside it for Custom; and the switch
// "Open and close sprints at recurring meetings", which the spec reads as Monday to Friday and which
// only means something for "1 week, 5 working days" (anywhere else the designer's switch answers
// with its own toast). Each change is one PATCH /api/team (owner and admin; a member sees the rows
// switched off). The preview is read again after every change because a new length redraws the
// open sprints (docs/fe-integration-guide-sprints.md). A refused change puts the control back and
// says why. The reminders row below is W8's and stays drawn off in settings-planned-sections.tsx.
export function SettingsSprintRows({ team, list }: { team: Team; list: SprintList | null }) {
  const today = useToday();
  const [value, setValue] = useState(team);
  const [days, setDays] = useState(String(team.sprint_custom_days));
  const [shown, setShown] = useState(list);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, flash] = useToast();
  const can = team.can_manage;

  async function save(changes: TeamChanges) {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      const next = await api.team.update(changes);
      setValue(next);
      setDays(String(next.sprint_custom_days));
      // The new length moved the dates: the line under the title comes from the sprints as they are now.
      setShown(await api.sprints.list().catch(() => null));
    } catch (caught) {
      setError(errorMessage(caught));
      setDays(String(value.sprint_custom_days));
    }
    setSaving(false);
  }

  function commitDays() {
    const typed = Math.max(1, Math.min(365, Math.round(Number(days)) || 1));
    setDays(String(typed));
    if (typed !== value.sprint_custom_days) void save({ sprint_custom_days: typed });
  }

  const weekdays = value.sprint_length === "week5";
  const current = shown?.sprints.find((s) => s.id === shown.current_id) ?? null;
  return (
    <>
      <div className="pl-row pl-row-center">
        <div>
          <div className="pl-row-title" id="sprint-length-label">
            Sprint length
          </div>
          {current ? <div className="pl-row-sub">{`Current sprint: ${current.name}, ${sprintRange(current, today)}`}</div> : null}
        </div>
        <div className="pl-controls">
          <select
            className="select pl-s36"
            aria-labelledby="sprint-length-label"
            value={value.sprint_length}
            disabled={!can || saving}
            onChange={(event) => void save({ sprint_length: event.target.value as SprintLength })}
          >
            {SPRINT_LENGTHS.map(([length, label]) => (
              <option key={length} value={length}>
                {label}
              </option>
            ))}
          </select>
          {value.sprint_length === "custom" ? (
            <div className="pl-days">
              <input
                className="input"
                type="number"
                min={1}
                max={365}
                aria-label="Days per sprint"
                value={days}
                disabled={!can || saving}
                onChange={(event) => setDays(event.target.value)}
                onBlur={commitDays}
                onKeyDown={(event) => {
                  if (event.key === "Enter") commitDays();
                }}
              />
              <span>days</span>
            </div>
          ) : null}
        </div>
      </div>
      <div className="pl-row">
        <div>
          <div className="pl-row-title" id="sprint-align-label">
            Open and close sprints at recurring meetings
          </div>
          <div className="pl-row-sub">
            {weekdays
              ? "Sprints open at Monday planning and close at Friday review."
              : "Available for 1 week, 5 working day sprints."}
          </div>
        </div>
        <Toggle
          checked={weekdays && value.sprint_align_weekdays}
          disabled={!can || saving}
          aria-labelledby="sprint-align-label"
          onClick={() => {
            if (!weekdays) return flash("Only available for 1 week, 5 working day sprints");
            void save({ sprint_align_weekdays: !value.sprint_align_weekdays });
          }}
        />
      </div>
      <div role="status" aria-live="polite">
        {error ? (
          <div className="set-note">
            <Alert kind="error">{error}</Alert>
          </div>
        ) : null}
      </div>
      <Toast message={toast} />
    </>
  );
}
