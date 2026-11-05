"use client";

import { useEffect, useRef, useState } from "react";
import { Alert, Button } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { InviteRole, TeamInviteResult } from "@/lib/api/types";
import { validEmail } from "@/lib/accounts";

// The designer's "Invite people" dialog (`inviteOpen` in Beyric Note Taker.dc.html): email chips
// (Enter or a comma adds one, a blur adds what is typed), a Role select (Admin or Member), Cancel and
// Send invites. LEAD-DESIGNED, not in the designer's file: the optional "Name" field under the
// chips, which appears when exactly one address is entered (the name becomes one of that person's
// aliases, so a meeting that says "Kemi" matches them: spec 2 row 4), built from the dialog's own
// Field style. Only the owner may invite an admin, so the Role select offers Admin to the owner only.
//
// One POST for the whole list. Each address answers on its own (docs/fe-integration-guide-team.md):
// `sent` and `resent` close the dialog and toast; an address that is already a member, or not an
// address, stays in the dialog with the reason. A whole-request refusal (`pilot_full`,
// `invite_limit`, `team_forbidden`) is shown as it is written by the API.
const OUTCOME_TEXT: Record<string, string> = {
  already_member: "is already on your team.",
  already_invited: "was invited a moment ago. Try again in a minute to send a new link.",
  invalid_email: "is not a valid email address.",
};

export function TeamInviteDialog({
  open,
  canInviteAdmin,
  initialName = "",
  onClose,
  onSent,
}: {
  open: boolean;
  canInviteAdmin: boolean;
  initialName?: string;
  onClose: () => void;
  onSent: (results: TeamInviteResult[]) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [emails, setEmails] = useState<string[]>([]);
  const [typed, setTyped] = useState("");
  const [name, setName] = useState(initialName);
  const [role, setRole] = useState<InviteRole>("member");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setEmails([]);
      setTyped("");
      setName(initialName);
      setRole("member");
      setError(null);
      setNotes([]);
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    if (!open && dialog.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
  }, [open, initialName]);

  function commit(raw = typed): string[] {
    const parts = raw
      .split(/[,\s]+/)
      .map((x) => x.trim().toLowerCase())
      .filter((x) => x && validEmail(x));
    const next = [...emails, ...parts.filter((p) => !emails.includes(p))];
    setEmails(next);
    setTyped("");
    return next;
  }

  async function send() {
    if (busy) return;
    const all = commit();
    if (all.length === 0) {
      setError("Enter at least one email address.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotes([]);
    try {
      const done = await api.team.invite(
        all.map((email) => ({ email, name: all.length === 1 && name.trim() ? name.trim() : null })),
        role,
      );
      const problems = done.results.filter((r) => !["sent", "resent"].includes(r.status));
      const sent = done.results.filter((r) => ["sent", "resent"].includes(r.status));
      if (problems.length === 0) {
        onSent(sent);
        onClose();
      } else {
        // What went out is done; what did not stays here with the reason.
        setEmails(problems.map((r) => r.email));
        setNotes(problems.map((r) => `${r.email} ${OUTCOME_TEXT[r.status] ?? "could not be invited."}`));
        if (sent.length) onSent(sent);
      }
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setBusy(false);
  }

  return (
    <dialog
      ref={ref}
      className="dialog tm-dialog"
      aria-labelledby="tm-invite-title"
      onClose={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      <div className="tm-dialog-head">
        <h2 className="dialog-title" id="tm-invite-title">
          Invite people
        </h2>
        <p className="tm-dialog-sub">They will get an email with a link to join your team.</p>
      </div>
      <div className="tm-dialog-body">
        <div className="field">
          <label className="field-label" htmlFor="tm-invite-input">
            Email addresses
          </label>
          <div className="tm-chipbox">
            {emails.map((email) => (
              <span className="tm-chip" key={email}>
                {email}
                <button
                  type="button"
                  aria-label={`Remove ${email}`}
                  onClick={() => setEmails((current) => current.filter((x) => x !== email))}
                >
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" aria-hidden="true">
                    <path d="M6 6l12 12M18 6 6 18" />
                  </svg>
                </button>
              </span>
            ))}
            <input
              id="tm-invite-input"
              value={typed}
              placeholder="name@company.com, press Enter"
              autoComplete="off"
              onChange={(event) => setTyped(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === ",") {
                  event.preventDefault();
                  commit();
                }
              }}
              onBlur={() => commit()}
            />
          </div>
        </div>
        {emails.length === 1 || (emails.length === 0 && initialName) ? (
          <div className="field">
            <label className="field-label" htmlFor="tm-invite-name">
              Name (optional)
            </label>
            <input
              className="input"
              id="tm-invite-name"
              value={name}
              maxLength={100}
              placeholder="What you call them in meetings"
              onChange={(event) => setName(event.target.value)}
            />
            <span className="field-hint">So a task that names them is matched to them once they join.</span>
          </div>
        ) : null}
        <div className="field">
          <label className="field-label" htmlFor="tm-invite-role">
            Role
          </label>
          <select
            id="tm-invite-role"
            className="select"
            value={role}
            onChange={(event) => setRole(event.target.value as InviteRole)}
          >
            {canInviteAdmin ? <option value="admin">Admin</option> : null}
            <option value="member">Member</option>
          </select>
        </div>
        {notes.length > 0 ? (
          <Alert kind="notice">
            {notes.map((line) => (
              <div key={line}>{line}</div>
            ))}
          </Alert>
        ) : null}
        {error ? <Alert kind="error">{error}</Alert> : null}
      </div>
      <div className="tm-dialog-foot">
        <Button kind="secondary" size="md" type="button" onClick={onClose}>
          Cancel
        </Button>
        <Button size="md" type="button" disabled={busy} onClick={() => void send()}>
          {busy ? "Sending..." : "Send invites"}
        </Button>
      </div>
    </dialog>
  );
}
