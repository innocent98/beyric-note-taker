"use client";

import { useState, type ChangeEvent } from "react";
import { Alert, Card, Checkbox, PageHead } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { EmailPreferences } from "@/lib/api/types";

// Settings. The designer's Settings screen (docs/ui, "Settings") has Calendar, Sprints and
// reminders, Tasks and Default notes language; it has no email section, so this one is a plain
// card in the same frame: one switch for the "notes ready" email, saved the moment it changes.
// A failed save puts the switch back and says so, so the screen never shows a state the server
// does not have. Sign-in mail (verification, password reset) is not part of this switch.
// The card's id is the target of the "Email preferences" link at the foot of the mail.
export function SettingsView({ email }: { email: EmailPreferences }) {
  const [notifyEmail, setNotifyEmail] = useState(email.notify_email);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function change(event: ChangeEvent<HTMLInputElement>) {
    if (saving) return;
    const wanted = event.target.checked;
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const stored = await api.settings.email.set(wanted);
      setNotifyEmail(stored.notify_email);
      setSaved(true);
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setSaving(false);
  }

  return (
    <div className="stack-md measure">
      <PageHead title="Settings" subtitle="Preferences for your notes and transcripts." />
      <div id="email-preferences">
        <Card title="Email preferences">
          <div className="stack-md">
            {error ? <Alert kind="error">{error}</Alert> : null}
            <Checkbox id="notify-email" checked={notifyEmail} disabled={saving} onChange={(event) => void change(event)}>
              <strong>Email me when my meeting notes are ready</strong>
              <br />
              <span className="muted">
                One email a meeting, with a short extract of the notes and a link to the full notes. Emails about
                signing in and your password are always sent.
              </span>
            </Checkbox>
            <div role="status" aria-live="polite" className="muted">
              {saving ? "Saving..." : saved ? "Saved." : ""}
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
