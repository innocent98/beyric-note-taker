"use client";

import { useEffect, useRef, useState } from "react";
import { Alert, AppLink, Button, Icon } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { Milestone, Project, Task, TaskChanges, TaskPerson } from "@/lib/api/types";
import { type SprintBook, sprintChoices } from "@/lib/sprints";
import {
  assignedLabel,
  avatarTone,
  COLUMNS,
  dueOptions,
  milestoneTitle,
  personInitials,
  sourceLabel,
  suggestedLead,
  transcriptHref,
} from "@/lib/tasks";

// A speaker is not a team member, so the circle's colour comes from the name (the same name, the same
// colour), not from a user id.
function speakerTone(name: string): number {
  return [...name].reduce((sum, ch) => sum + ch.charCodeAt(0), 0);
}

// The task drawer (the designer's `drawerOpen` panel): a side panel with the task's title, the
// notice about a spoken owner nobody on the team matched, the Status, Assignee, Due and Assigned
// rows, the quote and a link to its meeting, Delete task and Done. Each change is one PATCH of
// only that field (docs/fe-integration-guide-tasks.md); the answer is the whole task and replaces
// the one on screen. What the person may change comes with the task (`can_*`); a control they may
// not use is disabled, not hidden, and a refusal (403 `team_forbidden`) is shown in the panel.
//
// Milestone and Project (W4) are drawn when the lists are given: a milestone is one of the team's
// confirmed ones, a project one of its projects, and the "Check project" line, with its Confirm
// button, shows while the filing was a guess. Choosing a project is the confirmation. Sprint (W9) is
// drawn when the sprints are given: Backlog, the current and the next sprint of the task's project
// (planning is filing, so it is off unless `can_edit`). "Invite them to the team" opens the Team page
// with its invite dialog filled in (W3); a member, who may not invite, is told to ask.
//
// A suggested task (the AI was unsure of it) opens with the designer's amber box, "Suggested task,
// medium confidence." and the reason, with Accept and Dismiss; the parent does both (`onAccept`,
// `onDismiss`: the API call, the toast and the lists). Editing it leaves it suggested. "FROM THE
// MEETING" shows the evidence card: the verbatim quote and who said it (no time, none is stored; the
// designer's paraphrase line is dropped: lead-designed) and "Show in transcript".
export function TaskDrawer({
  task,
  people,
  today,
  projects = null,
  milestones = [],
  sprints = null,
  canInvite = false,
  onInvite,
  onAccept,
  onDismiss,
  suggestionBusy = false,
  onChange,
  onDelete,
  onClose,
}: {
  task: Task;
  people: TaskPerson[];
  today: string;
  // The team's projects and confirmed milestones; `null` when they could not be read (the rows
  // are then left out rather than drawn empty).
  projects?: Project[] | null;
  milestones?: Milestone[];
  // The team's sprints and those of projects with their own length; `null` when they could not be
  // read (the Sprint row is then left out).
  sprints?: SprintBook | null;
  // An owner or admin of the team may invite the person a meeting named (`GET /api/team` `can_manage`).
  canInvite?: boolean;
  onInvite?: (spokenName: string) => void;
  onAccept?: (task: Task) => void;
  onDismiss?: (task: Task) => void;
  suggestionBusy?: boolean;
  onChange: (task: Task) => void;
  onDelete: (id: number) => void;
  onClose: () => void;
}) {
  const [title, setTitle] = useState(task.title);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Who said the quote and on which row: the lists carry it for suggested tasks only, so the drawer of
  // any other task asks for the one task (the answer to `GET /api/tasks/{id}` has it). A failure here
  // leaves the quote without a speaker.
  const [found, setFound] = useState<{ id: number; speaker: string | null; line: number | null } | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const closeNow = useRef(onClose);
  useEffect(() => {
    closeNow.current = onClose;
  });

  // A different task, or the same one changed from outside (a drag on the board): show its title.
  const [shownTitle, setShownTitle] = useState(task.title);
  if (task.title !== shownTitle) {
    setShownTitle(task.title);
    setTitle(task.title);
  }
  useEffect(() => {
    const before = document.activeElement;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeNow.current();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (before instanceof HTMLElement) before.focus();
    };
  }, []);

  const needsEvidence = Boolean(task.quote && task.meeting && !task.suggested && task.quote_speaker === null);
  useEffect(() => {
    if (!needsEvidence) return;
    let live = true;
    Promise.resolve()
      .then(() => api.tasks.get(task.id))
      .then((one) => live && setFound({ id: task.id, speaker: one.quote_speaker, line: one.quote_line }))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [needsEvidence, task.id]);
  const evidence = found && found.id === task.id ? found : null;
  const speaker = task.quote_speaker ?? evidence?.speaker ?? null;
  const evidenceHref = transcriptHref({ meeting: task.meeting, quote_line: task.quote_line ?? evidence?.line ?? null });

  async function save(changes: TaskChanges) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      onChange(await api.tasks.update(task.id, changes));
    } catch (caught) {
      setError(errorMessage(caught));
      setTitle(task.title);
    }
    setBusy(false);
  }

  async function remove() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.tasks.remove(task.id);
      onDelete(task.id);
    } catch (caught) {
      setError(errorMessage(caught));
      setBusy(false);
    }
  }

  // Someone who may only take a task (a member) is offered "Unassigned" and themselves; an owner or
  // admin is offered the whole team.
  const assignable = task.can_assign ? people : people.filter((p) => p.is_you);
  const assigneeDisabled = !(task.can_assign || task.can_claim);
  const assignee = task.assignee ? String(task.assignee.user_id) : "";
  const dueChoices = dueOptions(today, task.due_date);
  const project = projects?.find((p) => p.id === task.project_id) ?? null;

  return (
    <div className="td-scrim" onClick={onClose}>
      <div className="td-panel" role="dialog" aria-modal="true" aria-label="Task" onClick={(event) => event.stopPropagation()}>
        <div className="td-bar">
          <span className="td-bar-title">Task</span>
          <button type="button" className="td-close" aria-label="Close" onClick={onClose} ref={closeRef}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </div>
        <div className="td-body">
          <textarea
            className="td-title"
            aria-label="Task title"
            rows={2}
            maxLength={200}
            placeholder="What needs to be done?"
            value={title}
            readOnly={!task.can_edit}
            onChange={(event) => setTitle(event.target.value)}
            onBlur={() => {
              if (task.can_edit && title !== task.title) void save({ title });
            }}
          />
          {error ? <Alert kind="error">{error}</Alert> : null}
          {task.suggested ? (
            <div className="td-suggest">
              <span>
                <span className="td-suggest-lead">{suggestedLead(task)}</span>
                {task.reason ? ` ${task.reason}` : ""}
              </span>
              {onAccept && onDismiss ? (
                <div className="td-suggest-actions">
                  <button
                    type="button"
                    className="td-suggest-accept"
                    disabled={busy || suggestionBusy || !task.can_decide}
                    onClick={() => onAccept(task)}
                  >
                    Accept
                  </button>
                  <button
                    type="button"
                    className="td-suggest-dismiss"
                    disabled={busy || suggestionBusy || !task.can_decide}
                    onClick={() => onDismiss(task)}
                  >
                    Dismiss
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}
          {!task.assignee && task.spoken_owner ? (
            <div className="td-notice">
              <span>&quot;{task.spoken_owner}&quot; was named as the owner in the meeting, but is not on your team.</span>
              {canInvite && onInvite ? (
                <button type="button" className="td-notice-link" onClick={() => onInvite(task.spoken_owner ?? "")}>
                  Invite them to the team
                </button>
              ) : (
                <span>Ask an owner or admin of your team to invite them.</span>
              )}
            </div>
          ) : null}
          {task.check_project && project ? (
            <div className="td-notice">
              <span>
                Filed under {project.name || "Untitled project"} from the meeting it came from, but it could belong
                elsewhere. Confirm or pick another project below.
              </span>
              {task.can_edit ? (
                <button type="button" className="td-confirm" disabled={busy} onClick={() => void save({ check_project: false })}>
                  Confirm {project.name || "Untitled project"}
                </button>
              ) : null}
            </div>
          ) : null}
          <div className="td-grid">
            <label htmlFor="td-status">Status</label>
            <select
              id="td-status"
              className="select td-select"
              value={task.status}
              disabled={!task.can_update || busy}
              onChange={(event) => void save({ status: event.target.value as Task["status"] })}
            >
              {COLUMNS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <label htmlFor="td-assignee">Assignee</label>
            <select
              id="td-assignee"
              className="select td-select"
              value={assignee}
              disabled={assigneeDisabled || busy}
              onChange={(event) => void save({ assignee_user_id: event.target.value ? Number(event.target.value) : null })}
            >
              <option value="">Unassigned</option>
              {assignable.map((p) => (
                <option key={p.user_id} value={p.user_id}>
                  {p.name}
                  {p.is_you ? " (you)" : ""}
                </option>
              ))}
            </select>
            <label htmlFor="td-due">Due</label>
            <select
              id="td-due"
              className="select td-select"
              value={task.due_date ?? ""}
              disabled={!task.can_update || busy}
              onChange={(event) => void save({ due_date: event.target.value || null })}
            >
              {dueChoices.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            {projects ? (
              <>
                <label htmlFor="td-milestone">Milestone</label>
                <select
                  id="td-milestone"
                  className="select td-select"
                  value={task.milestone_id ?? ""}
                  disabled={!task.can_edit || busy}
                  onChange={(event) => void save({ milestone_id: event.target.value ? Number(event.target.value) : null })}
                >
                  <option value="">None</option>
                  {milestones.map((m) => (
                    <option key={m.id} value={m.id}>
                      {milestoneTitle(m)}
                    </option>
                  ))}
                </select>
                <label htmlFor="td-project">Project</label>
                <select
                  id="td-project"
                  className="select td-select"
                  value={task.project_id ?? ""}
                  disabled={!task.can_edit || busy}
                  onChange={(event) => void save({ project_id: event.target.value ? Number(event.target.value) : null })}
                >
                  <option value="">No project</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name || "Untitled project"}
                    </option>
                  ))}
                </select>
              </>
            ) : null}
            {sprints ? (
              <>
                <label htmlFor="td-sprint">Sprint</label>
                <select
                  id="td-sprint"
                  className="select td-select"
                  value={task.sprint_id === null ? "" : String(task.sprint_id)}
                  disabled={!task.can_edit || busy}
                  onChange={(event) => void save({ sprint_id: event.target.value ? Number(event.target.value) : null })}
                >
                  {sprintChoices(sprints, task).map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </>
            ) : null}
            <span>Assigned</span>
            <span className="td-assigned">{assignedLabel(task.assigned_on, today)}</span>
          </div>
          {task.from_meeting ? (
            <div className="td-source">
              <div className="td-source-label">FROM THE MEETING</div>
              {task.quote ? (
                <div className="td-ev">
                  {speaker ? (
                    <div className="td-ev-who">
                      <span className={`avatar ${avatarTone(speakerTone(speaker))}`}>
                        {personInitials({ name: speaker })}
                      </span>
                      <span className="td-ev-name">{speaker}</span>
                    </div>
                  ) : null}
                  <div className="td-ev-text">“{task.quote}”</div>
                  {evidenceHref && speaker ? (
                    <AppLink className="td-ev-link" href={evidenceHref}>
                      <Icon name="video" size={12} />
                      Show in transcript
                    </AppLink>
                  ) : null}
                </div>
              ) : null}
              {task.meeting ? (
                <AppLink className="td-source-link" href={`/meetings/${task.meeting.id}`}>
                  <Icon name="video" size={12} />
                  {sourceLabel(task)}
                </AppLink>
              ) : null}
            </div>
          ) : null}
        </div>
        <div className="td-foot">
          {task.can_delete ? (
            <button type="button" className="td-delete" disabled={busy} onClick={() => void remove()}>
              Delete task
            </button>
          ) : (
            <span />
          )}
          <Button type="button" size="md" onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    </div>
  );
}
