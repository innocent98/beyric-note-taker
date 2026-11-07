"use client";

import { Icon } from "@/components/ui";
import type { Task } from "@/lib/api/types";
import { avatarTone, isOverdue, personInitials, sourceLabel, swatchClass, taskDueText } from "@/lib/tasks";

// The badges row, on a card and on a Backlog row: the project (dot and name, or "No project") while
// the board shows every project, "Carried over" and "Check project". Nothing when there is none.
export function TaskBadges({
  task,
  project = null,
  showProject = false,
}: {
  task: Task;
  project?: { name: string; color: string } | null;
  showProject?: boolean;
}) {
  const carried = task.carried && task.status !== "done";
  if (!showProject && !carried && !task.check_project) return null;
  return (
    <div className="tk-badges">
      {showProject ? (
        <span className="tk-proj">
          <span className={`tk-proj-dot ${swatchClass(project?.color)}`} />
          {project ? project.name || "Untitled project" : "No project"}
        </span>
      ) : null}
      {carried ? <span className="tk-carried">Carried over</span> : null}
      {task.check_project ? <span className="tk-check">Check project</span> : null}
    </div>
  );
}

// A task as the board draws it (the designer's card): the title, a "New" pill, the meeting it came
// from, the due day and who has it, or "Needs owner". Titles and sources are plain text, always.
// The badges row: the project (dot and name, or "No project") while the board shows every project,
// "Carried over" when a closed sprint left it unfinished (W9), and "Check project" when the filing
// was a guess, in that order.
export function TaskCard({
  task,
  today,
  project = null,
  showProject = false,
  dragging,
  onOpen,
  onDragStart,
  onDragEnd,
}: {
  task: Task;
  today: string;
  // The task's project (null: none) and whether to draw it, which the board does on "All projects".
  project?: { name: string; color: string } | null;
  showProject?: boolean;
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
      <TaskBadges task={task} project={project} showProject={showProject} />
      <div className="tk-card-foot">
        <span className={`tk-due${done ? " tk-due-done" : isOverdue(task.due_date, today) ? " tk-due-overdue" : task.due_date === today ? " tk-due-today" : ""}`}>
          <Icon name="calendar" size={12} />
          {taskDueText(task, today)}
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
