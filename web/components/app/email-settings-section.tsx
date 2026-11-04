"use client";

import { useState } from "react";
import { Alert, Toggle } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { EmailPreferences } from "@/lib/api/types";

// The "Email" section of Settings. The designer's Settings screen (docs/ui, "Settings") has no
// email section, so this one reuses its section pattern exactly (section card, title with a grey
// line under it, a row with a label, a description and the switch used by "Create tasks from
// action items") and is a DESIGNER GAP to review (docs/sop/2026-11-04-prelaunch-mail-legal-prefs.md).
// The switch saves the moment it is flipped. A failed save leaves it where it was and says so, so
// the screen never shows a state the server does not have. Sign-in mail is not part of it.
// The section's id is the target of the "Email preferences" link at the foot of the mail.
export function EmailSettingsSection({ email }: { email: EmailPreferences }) {
  const [on, setOn] = useState(email.notify_email);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function flip() {
    if (saving) return;
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const stored = await api.settings.email.set(!on);
      setOn(stored.notify_email);
      setSaved(true);
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setSaving(false);
  }

  return (
    <section className="set-section" id="email-preferences" aria-labelledby="email-title">
      <div className="set-head">
        <div className="set-title" id="email-title">
          Email
        </div>
        <div className="set-desc">What the note taker emails you.</div>
      </div>
      <div className="set-row">
        <div>
          <div className="set-row-label" id="notify-email-label">
            Email me when my notes are ready
          </div>
          <div className="set-row-desc" id="notify-email-desc">
            One email a meeting, with a short extract of the notes and a link to the full notes. Emails about signing
            in and your password are always sent.
          </div>
        </div>
        <Toggle
          checked={on}
          disabled={saving}
          aria-labelledby="notify-email-label"
          aria-describedby="notify-email-desc"
          onClick={() => void flip()}
        />
      </div>
      {/* Drawn only when there is something to say, so an idle section is exactly the designer's. */}
      <div role="status" aria-live="polite">
        {error || saving || saved ? (
          <div className="set-note">
            {error ? <Alert kind="error">{error}</Alert> : null}
            {saving ? "Saving..." : saved ? "Saved." : ""}
          </div>
        ) : null}
      </div>
    </section>
  );
}
