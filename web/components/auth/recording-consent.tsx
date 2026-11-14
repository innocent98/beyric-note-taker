"use client";

import { useState, type Ref } from "react";
import { Button, Checkbox } from "@/components/ui";

// The recording-consent box, one wording everywhere (sign-up, the team-invite sign-up, and the card on
// the Meetings page for accounts older than the box). The words are the owner's, approved 2026-11-14:
// do not reword them. The server stores which wording was accepted (`RECORDING_ACK_VERSION` in
// app/signup.py): change both together.
//
// LEAD-DESIGNED, not in the designer's files (2026-11-14): Auth.dc.html has no such box. It is the
// designer's own terms checkbox (the `Checkbox` component), unticked at first, with a quiet text link
// under it that opens the one-line example; no new component, no new style.
export const RECORDING_ACK_TEXT =
  "I understand that I'm responsible for telling the people in my meetings that Beyric Note Taker will record and transcribe them, and for getting their consent wherever the law requires it.";
export const RECORDING_HOW_TITLE = "How to tell attendees";
export const RECORDING_HOW_TEXT =
  "One line in the invite or at the start of the call is enough, for example: 'I'm using Beyric to record this call and take notes.'";
export const RECORDING_ACK_MESSAGE = "Confirm that you will tell the people in your meetings that Beyric will record them.";

// "How to tell attendees": a link that shows the example under it, and hides it again.
export function HowToTell() {
  const [open, setOpen] = useState(false);
  return (
    <div className="auth-small">
      <Button kind="link" type="button" aria-expanded={open} aria-controls="how-to-tell" onClick={() => setOpen((value) => !value)}>
        {RECORDING_HOW_TITLE}
      </Button>
      {open ? <p id="how-to-tell">{RECORDING_HOW_TEXT}</p> : null}
    </div>
  );
}

// The sign-up box: never pre-ticked, with its example under it.
export function RecordingBox({
  checked,
  error,
  onChange,
  ref,
}: {
  checked: boolean;
  error?: string;
  onChange: (checked: boolean) => void;
  ref?: Ref<HTMLInputElement>;
}) {
  return (
    <div className="auth-stack-sm">
      <Checkbox id="accept_recording" ref={ref} checked={checked} error={error} onChange={(event) => onChange(event.target.checked)}>
        {RECORDING_ACK_TEXT}
      </Checkbox>
      <HowToTell />
    </div>
  );
}
