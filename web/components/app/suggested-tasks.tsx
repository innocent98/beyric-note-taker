"use client";

import { AppLink } from "@/components/ui";
import type { Task } from "@/lib/api/types";
import { avatarTone, confidenceLabel, personInitials, transcriptHref } from "@/lib/tasks";

// The Tasks screen's "Suggested tasks" section (the designer's `hasSuggested` block): the action
// items the AI was not sure about, waiting for a person. Drawn only when there are some. Each row has
// the title and the confidence pill, the evidence box (the verbatim quote and who said it: no time,
// none is stored, and no paraphrase line: lead-designed, not in the designer's file), the reason in
// amber, the assignee or "No owner", and Show source, Edit, Dismiss and Accept. Titles, quotes and
// reasons are plain text. On a phone the four buttons stack under the row's text (lead-designed).
export function SuggestedTasks({
  tasks,
  busyId,
  onAccept,
  onDismiss,
  onEdit,
}: {
  tasks: Task[];
  busyId: number | null;
  onAccept: (task: Task) => void;
  onDismiss: (task: Task) => void;
  onEdit: (task: Task) => void;
}) {
  if (tasks.length === 0) return null;
  // Oldest first, the order the notes list them in (the designer's rows follow it too); the API's own
  // order is newest first.
  const ordered = [...tasks].sort((a, b) => a.id - b.id);
  return (
    <section className="tk-sg" aria-label="Suggested tasks">
      <div className="tk-sg-head">
        <div className="tk-sg-title">
          <span className="tk-sg-name">Suggested tasks</span>
          <span className="tk-sg-count">{tasks.length} to confirm</span>
        </div>
        <div className="tk-sg-sub">
          These action items were not clear enough to add on their own. Check where each one came from, then accept or
          dismiss it.
        </div>
      </div>
      {ordered.map((task) => {
        const href = transcriptHref(task);
        const busy = busyId === task.id;
        return (
          <div className="tk-sg-row" key={task.id}>
            <div className="tk-sg-main">
              <div className="tk-sg-line">
                <span className="tk-sg-task">{task.title || "Untitled task"}</span>
                {task.confidence ? (
                  <span className={`tk-conf tk-conf-${task.confidence}`}>{confidenceLabel(task.confidence)}</span>
                ) : null}
              </div>
              {task.quote ? (
                <div className="tk-sg-ev">
                  {task.quote_speaker ? <span className="tk-sg-who">{task.quote_speaker}</span> : null}
                  <span>“{task.quote}”</span>
                </div>
              ) : null}
              {task.reason ? <div className="tk-sg-reason">{task.reason}</div> : null}
            </div>
            <div className="tk-sg-side">
              {task.assignee ? (
                <span className="tk-sg-owner">
                  <span className={`avatar ${avatarTone(task.assignee.user_id)}`}>{personInitials(task.assignee)}</span>
                  {task.assignee.name}
                </span>
              ) : (
                <span className="tk-sg-none">No owner</span>
              )}
              {href ? (
                <AppLink className="tk-sg-btn" href={href}>
                  Show source
                </AppLink>
              ) : null}
              <button type="button" className="tk-sg-btn" onClick={() => onEdit(task)}>
                Edit
              </button>
              <button type="button" className="tk-sg-btn" disabled={busy || !task.can_delete} onClick={() => onDismiss(task)}>
                Dismiss
              </button>
              <button
                type="button"
                className="tk-sg-btn tk-sg-accept"
                disabled={busy || !task.can_edit}
                onClick={() => onAccept(task)}
              >
                Accept
              </button>
            </div>
          </div>
        );
      })}
    </section>
  );
}
