"use client";

import { useEffect, useRef, useState } from "react";
import { Button, Card, SelectField } from "@/components/ui";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { Meeting, TeamSummary } from "@/lib/api/types";

// The exact words of his decision (spec 9.1, docs/fe-integration-guide-teams.md section 5). Do not reword.
export const MOVE_WARNING = "This meeting will belong to the team. It stays with the team even if you leave or delete your account.";
export const FORMER_MEMBER = "Former member";

// What the page says when a share, a move or a move back is refused. The codes are the guide's
// (sections 4 to 6); their `message` is written for the person, except that 404 and 422 are not shown
// by `errorMessage` elsewhere, so the ones that are about the team are spelled out here.
const OWN_WORDS = new Set([
  "team_required",
  "personal_team",
  "meeting_team_owned",
  "meeting_not_ready",
  "already_moved",
  "team_forbidden",
  "not_moved",
  "recorder_gone",
  "multi_team_disabled",
]);
export function teamActionMessage(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (OWN_WORDS.has(caught.code)) return caught.message;
    // 404 `not_found` "No such team.": they left it, or it is gone. The page and the switcher are refreshed.
    if (caught.status === 404 && caught.message === "No such team.") return "You are no longer in that team. Choose another.";
  }
  return errorMessage(caught);
}

type Props = {
  meeting: Meeting;
  // Every team the person is in (`GET /api/teams`); the personal space is never offered.
  teams: TeamSummary[];
  busy: boolean;
  onShare: (teamId: number) => void;
  onUnshare: () => void;
  onMove: (teamId: number) => void;
  onMoveBack: () => void;
};

// LEAD-DESIGNED, not in the designer's file (owner's request 2026-11-14, multi-team stage 3; for the
// designer to review): the meeting page's team card. Replaces the old "Share with team" switch while the
// server's multi-team switch is on. A private meeting: choose one of your teams, then Share (they read it,
// you stay its owner) or Move (the team owns it, after a confirmation in his words). A shared meeting: the
// team it is shared with, Stop sharing, or share with another team instead. A moved meeting: whose it is,
// who recorded it ("Former member" when that account is gone) and, for the owning team's owner or admin,
// Move back. Built from the side cards, the select field and the buttons.
export function MeetingTeamCard({ meeting: m, teams, busy, onShare, onUnshare, onMove, onMoveBack }: Props) {
  const options = teams.filter((t) => !t.is_personal);
  const [picked, setPicked] = useState<number | null>(null);
  const [asking, setAsking] = useState<"move" | "back" | null>(null);
  const selected = picked ?? m.team?.id ?? options[0]?.id ?? null;
  const selectedTeam = options.find((t) => t.id === selected) ?? null;

  if (m.owned_by_team) {
    const gone = m.shared_by === FORMER_MEMBER;
    const recorder = m.shared_by ?? "you";
    return (
      <Card compact>
        <h2 className="sh-title" id="share-team-label">
          Belongs to {m.team?.name ?? "a team"}
        </h2>
        <p className="sh-sub" id="share-team-sub">
          Everyone on the team can read these notes and the transcript. It stays with the team even if the person who
          recorded it leaves or deletes their account.
        </p>
        <p className="sh-sub mt-note">
          Recorded by {m.shared_by ?? "you"}.
          {gone ? " That account is gone, so it cannot be given back." : ""}
        </p>
        {m.can_move_back && !gone ? (
          <div className="mt-actions">
            <Button kind="secondary" size="md" type="button" disabled={busy} onClick={() => setAsking("back")}>
              Move back to {recorder}
            </Button>
          </div>
        ) : null}
        <ConfirmDialog
          open={asking === "back"}
          title={`Move this meeting back to ${recorder}?`}
          confirm="Move back"
          busy={busy}
          onCancel={() => setAsking(null)}
          onConfirm={() => {
            setAsking(null);
            onMoveBack();
          }}
        >
          <p>It will be private to {recorder} again and will leave {m.team?.name ?? "the team"}.</p>
          <p>
            Tasks from this meeting go back to {recorder}; teammates&apos; assignments are removed. Posts already made to Slack
            stay where they are.
          </p>
        </ConfirmDialog>
      </Card>
    );
  }

  // Nothing to share with, and nothing to undo: the card is not drawn.
  if (!m.can_share || (options.length === 0 && !m.shared_with_team)) return null;

  const shared = m.shared_with_team && m.team !== null;
  return (
    <Card compact>
      <h2 className="sh-title" id="share-team-label">
        {shared ? `Shared with ${m.team?.name}` : "Share with a team"}
      </h2>
      <p className="sh-sub" id="share-team-sub">
        {shared
          ? "Everyone on that team can read these notes, the transcript and the tasks. You stay the owner."
          : "Private to you. Share it with one of your teams so they can read the notes, transcript and tasks."}
      </p>
      {options.length > 0 ? (
        <div className="mt-fields">
          <SelectField
            id="meeting-team"
            label="Team"
            compact
            value={selected ?? ""}
            disabled={busy}
            onChange={(event) => setPicked(Number(event.target.value))}
            options={options.map((t) => [t.id, t.name] as const)}
          />
        </div>
      ) : null}
      <div className="mt-actions">
        {shared ? (
          <>
            <Button kind="secondary" size="md" type="button" disabled={busy} onClick={onUnshare}>
              Stop sharing
            </Button>
            {selected !== null && selected !== m.team?.id ? (
              <Button size="md" type="button" disabled={busy} onClick={() => onShare(selected)}>
                Share with {selectedTeam?.name} instead
              </Button>
            ) : null}
          </>
        ) : selected !== null ? (
          <Button size="md" type="button" disabled={busy} onClick={() => onShare(selected)}>
            Share
          </Button>
        ) : null}
        {m.can_move && selected !== null ? (
          <Button kind="secondary" size="md" type="button" disabled={busy} onClick={() => setAsking("move")}>
            Move to team
          </Button>
        ) : null}
      </div>
      {shared ? (
        <p className="sh-sub mt-note">Stopping makes it private again. Tasks from this meeting go back to you; teammates&apos; assignments are removed.</p>
      ) : null}
      {m.can_move ? <p className="sh-sub mt-note">Sharing lets the team read it. Moving gives it to the team.</p> : null}
      <ConfirmDialog
        open={asking === "move"}
        title={`Move this meeting to ${selectedTeam?.name ?? "the team"}?`}
        confirm="Move meeting"
        busy={busy}
        onCancel={() => setAsking(null)}
        onConfirm={() => {
          setAsking(null);
          if (selected !== null) onMove(selected);
        }}
      >
        <p>{MOVE_WARNING}</p>
        <p>
          Its tasks move to the team&apos;s board, and anyone assigned who is not on the team is unassigned. Posts already made
          to Slack stay where they are.
        </p>
      </ConfirmDialog>
    </Card>
  );
}

// A modal question on a native <dialog>: the browser moves focus into it, keeps Tab inside, and Escape
// cancels; focus returns to the button that opened it. Built from the dialog the meeting page already uses.
function ConfirmDialog({
  open,
  title,
  confirm,
  busy,
  onCancel,
  onConfirm,
  children,
}: {
  open: boolean;
  title: string;
  confirm: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    if (!open && dialog.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
  }, [open]);
  const id = `confirm-${title.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <dialog
      ref={ref}
      className="dialog"
      aria-labelledby={id}
      onClose={onCancel}
      onClick={(event) => {
        if (event.target === ref.current) onCancel();
      }}
    >
      <h2 className="dialog-title" id={id}>
        {title}
      </h2>
      {children}
      <div className="dialog-actions">
        <Button kind="secondary" size="md" type="button" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="md" type="button" disabled={busy} onClick={onConfirm}>
          {confirm}
        </Button>
      </div>
    </dialog>
  );
}
