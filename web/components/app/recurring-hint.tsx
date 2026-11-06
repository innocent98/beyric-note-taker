"use client";

import { useState, useSyncExternalStore } from "react";
import { Alert } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";

// LEAD-DESIGNED, not in the designer's file (approved by Adebayo, 2026-11-06): the "Make this a
// recurring meeting?" chip (spec 7, D12), drawn for a meeting whose link was used before
// (`suggest_recurring`). It uses the amber pill of "Needs owner" and the file's quiet text buttons.
// "Make recurring" asks the server to make the series from this link; "Not now" hides the chip for
// this meeting in this browser (nothing is stored on the server).
const KEY = (id: number) => `series-hint-dismissed-${id}`;

// Whether this browser has dismissed the chip. Read from storage after hydration (the server render
// has no storage and draws nothing), so a reload never flashes a chip that was dismissed.
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => void listeners.delete(listener);
};
function dismissed(id: number): boolean {
  try {
    return window.localStorage.getItem(KEY(id)) === "1";
  } catch {
    return false; // no storage: the chip shows, and "Not now" lasts until the page reloads
  }
}
const memory = new Set<number>();

export function RecurringHint({ meetingId, onDone }: { meetingId: number; onDone: () => void }) {
  const hidden = useSyncExternalStore(
    subscribe,
    () => memory.has(meetingId) || dismissed(meetingId),
    () => true,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (hidden) return null;
  async function make() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.meetings.makeRecurring(meetingId);
      onDone();
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setBusy(false);
  }
  function dismiss() {
    memory.add(meetingId);
    try {
      window.localStorage.setItem(KEY(meetingId), "1");
    } catch {
      // nothing to remember it in beyond this page
    }
    listeners.forEach((listener) => listener());
  }

  return (
    <div className="rh">
      <div className="rh-chip">
        <span className="rh-text">Make this a recurring meeting?</span>
        <button type="button" className="rh-btn" disabled={busy} onClick={() => void make()}>
          Make recurring
        </button>
        <button type="button" className="rh-btn rh-btn-quiet" onClick={dismiss}>
          Not now
        </button>
      </div>
      <div role="status" aria-live="polite">
        {error ? <Alert kind="error">{error}</Alert> : null}
      </div>
    </div>
  );
}
