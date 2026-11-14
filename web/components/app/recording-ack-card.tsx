"use client";

import { useState } from "react";
import { Alert, Button } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import { RECORDING_ACK_TEXT, HowToTell } from "@/components/auth/recording-consent";

// The one-time card for accounts older than the sign-up recording box: the same words, an "I
// understand" button that stores the acknowledgement (POST /api/settings/recording-ack), and the
// "How to tell attendees" example. The server says whether it is needed (`recording_acknowledged` on
// `GET /api/session`): once it is stored the card is gone for good.
//
// It NEVER blocks anything: the form, the calendar and the bot all work with the card showing, and a
// failed save only shows a message under the text (the next visit shows the card again).
//
// LEAD-DESIGNED, not in the designer's files (2026-11-14). It borrows the quiet bordered box of the
// calendar tip (`.invite-tip`: the colours and radius of the rule cards on Settings) and the app's
// own buttons; the designer can redraw it, the words and the placement are the part to keep.
export function RecordingAckCard({ acknowledged }: { acknowledged: boolean }) {
  const [done, setDone] = useState(acknowledged);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (done) return null;

  async function acknowledge() {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await api.settings.recordingAck();
      setDone(true);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setPending(false);
    }
  }

  return (
    <aside className="invite-tip" aria-label="Recording consent" data-card="recording-ack">
      <div className="invite-tip-text">
        <p className="invite-tip-body">{RECORDING_ACK_TEXT}</p>
        <HowToTell />
        {error ? (
          <Alert kind="error" role="alert">
            {error}
          </Alert>
        ) : null}
      </div>
      <Button kind="secondary" size="sm" type="button" onClick={acknowledge} disabled={pending}>
        {pending ? "Saving..." : "I understand"}
      </Button>
    </aside>
  );
}
