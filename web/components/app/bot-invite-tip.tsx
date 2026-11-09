"use client";

import { useRef } from "react";
import { CopyButton } from "./copy-button";

// "Add note@... to your event to skip the wait": a small tip with a Copy button, for people whose
// note taker joins Google Meet meetings. The server decides whether it applies and sends the address
// (`bot_invite_email` on `GET /api/calendar` and on a meeting): with no address nothing is drawn,
// and the address is never written in the web code. It is a public address, not a secret.
//
// LEAD-DESIGNED, not in the designer's file (2026-11-09): the designer drew no such tip. It uses the
// app's own pieces: a quiet bordered box (the colours and radius of the rule cards on Settings), the
// secondary small copy button the notes and transcript use, and the muted `meta` line for the limit.
// The designer can redraw it; the words and the placement are the part to keep.
//
// The wording is honest about the limit and does not promise: it skips the wait on Google Meet only,
// Zoom and Teams meetings still ask the host to let the note taker in.
export function BotInviteTip({ email }: { email: string | null | undefined }) {
  const address = useRef<HTMLElement>(null);
  if (!email) return null;

  // When the browser refuses to copy (no clipboard access, an old browser), select the address so
  // Ctrl/Cmd+C finishes the job: the button is never a dead end.
  function selectAddress() {
    const node = address.current;
    const selection = window.getSelection();
    if (!node || !selection) return;
    const range = document.createRange();
    range.selectNodeContents(node);
    selection.removeAllRanges();
    selection.addRange(range);
  }

  return (
    <aside className="invite-tip" aria-label="Tip: skip the wait on Google Meet">
      <div className="invite-tip-text">
        <p className="invite-tip-title">
          Add{" "}
          <code ref={address} className="invite-tip-address">
            {email}
          </code>{" "}
          to your event to skip the wait
        </p>
        <p className="invite-tip-body">
          Put it on the event&apos;s guest list and the note taker gets in without waiting for you to admit it.
        </p>
        <p className="meta">
          This only skips the wait on Google Meet. Zoom and Teams meetings still ask the host to admit the note taker.
        </p>
      </div>
      <CopyButton text={email} label="Copy address" onFailed={selectAddress} />
    </aside>
  );
}
