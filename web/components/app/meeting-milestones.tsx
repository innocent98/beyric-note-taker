"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { Milestone } from "@/lib/api/types";
import { milestoneTitle } from "@/lib/tasks";

// The milestones that came from one meeting (GET /api/meetings/{id}/milestones), for its page:
// the confirmed ones become the Details row "Milestone" (the designer's `curMilestone`), and a date
// the model heard but nobody has confirmed is offered as "Set milestone: <name>, <date>?" with a
// Confirm button. The offer is LEAD-DESIGNED, not in the designer's file (built from the amber
// notice pattern of the task drawer). Only an owner or admin of the team can confirm
// (`can_confirm`), so nobody else is shown a button that would be refused; a failed read draws
// nothing. Read again when the meeting's extraction finishes (`extractionStatus`).
export function useMeetingMilestones(meetingId: number, ready: boolean, extractionStatus: string | null) {
  const [rows, setRows] = useState<Milestone[]>([]);
  useEffect(() => {
    if (!ready) return;
    let live = true;
    api.milestones.forMeeting(meetingId).then(
      (answer) => live && setRows(answer.milestones),
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [meetingId, ready, extractionStatus]);
  return [rows, setRows] as const;
}

// The Details row: the confirmed milestones of this meeting, one line each.
export function MilestoneRows({ rows }: { rows: Milestone[] }) {
  const confirmed = rows.filter((m) => m.confirmed_at !== null);
  if (confirmed.length === 0) return null;
  return (
    <>
      <dt>Milestone</dt>
      <dd>
        {confirmed.map((m) => (
          <div key={m.id}>{milestoneTitle(m)}</div>
        ))}
      </dd>
    </>
  );
}

export function MilestoneProposals({ rows, onConfirmed }: { rows: Milestone[]; onConfirmed: (next: Milestone) => void }) {
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const proposals = rows.filter((m) => m.confirmed_at === null && m.can_confirm);
  if (proposals.length === 0) return null;
  async function confirm(m: Milestone) {
    setBusy(m.id);
    setError(null);
    try {
      onConfirmed(await api.milestones.confirm(m.id));
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setBusy(null);
  }
  return (
    <>
      {proposals.map((m) => (
        <div className="ms-propose" key={m.id}>
          <span>Set milestone: {milestoneTitle(m)}?</span>
          {m.quote ? <span className="ms-propose-quote">&ldquo;{m.quote}&rdquo;</span> : null}
          <button type="button" className="ms-propose-btn" disabled={busy !== null} onClick={() => void confirm(m)}>
            Confirm
          </button>
        </div>
      ))}
      {error ? (
        <p className="meta" role="alert">
          {error}
        </p>
      ) : null}
    </>
  );
}
