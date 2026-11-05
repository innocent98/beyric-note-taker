"use client";

import { useState } from "react";
import { Alert, Toggle } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { TasksSettings } from "@/lib/api/types";

// The first two rows of Settings > Tasks, live: "Create tasks from action items" and "Email
// assignees" (docs/fe-integration-guide-tasks.md, GET and PUT /api/settings/tasks). The switch saves
// the moment it is flipped; a failed save leaves it where it was and says so, so the screen never
// shows a state the server does not have. The rows below them (project tool, Slack) are W10 and stay
// drawn off in settings-planned-sections.tsx.
export function SettingsTasksRows({ initial }: { initial: TasksSettings }) {
  const [value, setValue] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function flip(key: keyof TasksSettings) {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      setValue(await api.settings.tasks.set({ ...value, [key]: !value[key] }));
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setSaving(false);
  }

  return (
    <>
      <div className="pl-row">
        <div>
          <div className="pl-row-title" id="tasks-auto-label">
            Create tasks from action items
          </div>
          <div className="pl-row-sub">Each action item becomes a task assigned to the teammate named as its owner.</div>
        </div>
        <Toggle
          checked={value.auto_create_tasks}
          disabled={saving}
          aria-labelledby="tasks-auto-label"
          onClick={() => void flip("auto_create_tasks")}
        />
      </div>
      <div className="pl-row">
        <div>
          <div className="pl-row-title" id="tasks-email-label">
            Email assignees
          </div>
          <div className="pl-row-sub">Send each person a list of their new tasks after the meeting.</div>
        </div>
        <Toggle
          checked={value.email_assignees}
          disabled={saving}
          aria-labelledby="tasks-email-label"
          onClick={() => void flip("email_assignees")}
        />
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
