"use client";

import { useRef, useState } from "react";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";

// Send now (docs/fe-integration-guide-calendar-send-now.md), shared by the Not recorded list and the
// Upcoming rows. LEAD-DESIGNED (2026-11-08): the designer's file draws no Send now, no toast for it
// and no failed send. The toast is the designer's own pill (`useToast`); a failed send keeps the row
// and its button and says why under the row, in the server's words.
//
// `send(joinId)` takes the row's `join_id` (never an event id). While a call is out the same row's
// button is disabled and a second press is ignored (the server also answers a repeat with the same
// meeting). A refusal changes nothing on the server, so the row stays. When the answer says the row
// itself has changed behind the page (the meeting is over, the join is not sendable, it is gone),
// the page is read again (`onChanged`) so the row says so.
export type SendNowControl = {
  sending: ReadonlySet<number>;
  errors: Readonly<Record<number, string>>;
  // Rows the server accepted: drawn gone until the page is read again.
  sent: ReadonlySet<number>;
  send: (joinId: number) => void;
};

const GONE_MESSAGE = "This meeting is no longer on your list.";
// Refusals that mean the row is not what the page thought; the others (busy, hours, host) are
// "not sent, try again" and leave the row as it is.
const ROW_CHANGED = new Set(["meeting_ended", "cannot_send_now", "account_suspended"]);

export function useSendNow({
  flash,
  onChanged,
}: {
  flash: (message: string) => void;
  onChanged: () => void;
}): SendNowControl {
  const [sending, setSending] = useState<ReadonlySet<number>>(new Set());
  const [sent, setSent] = useState<ReadonlySet<number>>(new Set());
  const [errors, setErrors] = useState<Record<number, string>>({});
  const going = useRef(new Set<number>());

  function send(joinId: number) {
    if (going.current.has(joinId)) return;
    going.current.add(joinId);
    setSending(new Set(going.current));
    setErrors((current) => Object.fromEntries(Object.entries(current).filter(([id]) => Number(id) !== joinId)));
    void (async () => {
      try {
        const result = await api.calendar.sendNow(joinId);
        setSent((current) => new Set(current).add(joinId));
        flash(result.status_text);
        onChanged();
      } catch (caught) {
        const gone = caught instanceof ApiError && caught.status === 404;
        setErrors((current) => ({ ...current, [joinId]: gone ? GONE_MESSAGE : errorMessage(caught) }));
        if (gone || (caught instanceof ApiError && ROW_CHANGED.has(caught.code))) onChanged();
      }
      going.current.delete(joinId);
      setSending(new Set(going.current));
    })();
  }

  return { sending, errors, sent, send };
}
