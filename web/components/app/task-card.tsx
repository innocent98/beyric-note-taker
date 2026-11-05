"use client";

import { Icon } from "@/components/ui";
import type { Task } from "@/lib/api/types";
import { avatarTone, dueLabel, personInitials, sourceLabel } from "@/lib/tasks";

// A task as the board draws it (the designer's card): the title, a "New" pill, the meeting it came
// from, the due day and who has it, or "Needs owner". Titles and sources are plain text, always.
// The project dot, "Carried over" and "Check project" badges wait for projects (W4) and sprints (W9).
export function TaskCard({
  task,
  today,
  dragging,
  onOpen,
  onDragStart,
  onDragEnd,
}: {
  task: Task;
  today: string;
  dragging?: boolean;
  onOpen: () => void;
  onDragStart: (event: React.DragEvent) => void;
  onDragEnd: () => void;
}) {
  const done = task.status === "done";
  return (
    <div
      className={`tk-card${dragging ? " tk-card-dragging" : ""}`}
      draggable={task.can_update}
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
    >
      <div className="tk-card-top">
        <div className={`tk-card-title${done ? " tk-done" : ""}`}>{task.title || "Untitled task"}</div>
        {task.is_new && !done ? <span className="tk-new">New</span> : null}
      </div>
      <div className="tk-source">
        <Icon name="video" size={12} />
        <span>{sourceLabel(task)}</span>
      </div>
      <div className="tk-card-foot">
        <span className={`tk-due${done ? " tk-due-done" : task.due_date === today ? " tk-due-today" : ""}`}>
          <Icon name="calendar" size={12} />
          {dueLabel(task.due_date, today)}
        </span>
        {task.assignee ? (
          <div className={`avatar tk-avatar ${avatarTone(task.assignee.user_id)}`} title={task.assignee.name}>
            {personInitials(task.assignee)}
          </div>
        ) : (
          <span className="tk-owner-pill">Needs owner</span>
        )}
      </div>
    </div>
  );
}
