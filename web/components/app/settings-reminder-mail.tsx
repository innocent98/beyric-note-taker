"use client";

import { useState } from "react";
import { Alert, Toggle } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { ReminderPreferences } from "@/lib/api/types";

// LEAD-DESIGNED, not in the designer's file (approved by Adebayo, 2026-11-06): the person's own
// switch for agenda and reminder mail, in the "Sprints and reminders" card under the team's
// reminders row, in that card's row pattern (GET and PUT /api/settings/reminders). It is the
// person's choice, so it is live for everyone, not only owners and admins. The server checks it
// again when each mail is sent. A failed save leaves it where it was and says so.
export function SettingsReminderMail({ initial }: { initial: ReminderPreferences }) {
  const [value, setValue] = useState(initial.notify_reminders);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function flip() {
    if (saving) return;
    setSaving(true);
    setError(null);
    try {
      setValue((await api.settings.reminders.set(!value)).notify_reminders);
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setSaving(false);
  }

  return (
    <>
      <div className="pl-row">
        <div>
          <div className="pl-row-title" id="remind-mail-label">
            Email me agendas and reminders
          </div>
          <div className="pl-row-sub">
            The agenda a teammate sends and the reminders before recurring meetings you attend. Only you can change this.
          </div>
        </div>
        <Toggle checked={value} disabled={saving} aria-labelledby="remind-mail-label" onClick={() => void flip()} />
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
