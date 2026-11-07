"use client";

import { Icon } from "@/components/ui";
import { TaskBadges } from "@/components/app/task-card";
import type { Task } from "@/lib/api/types";
import { avatarTone, isOverdue, personInitials, sourceLabel, taskDueText } from "@/lib/tasks";

export type BacklogSection = { key: string; title: string; sub: string; emptyText: string; tasks: Task[] };

// The Backlog view of the Tasks screen (the designer's `isBacklog`): two cards, the next sprint and
// the Backlog, each a list of open tasks with its title, meeting, badges, due day, owner and a
// Sprint select that plans the task (Backlog, the current or the next sprint). The select is the
// designer's; it is switched off for someone who may not plan the task (`can_edit`: owner, admin and
// the maker of a manual task), so it is never offered and then refused. A click on the select does
// not open the drawer.
export function BacklogView({
  sections,
  today,
  projectOf,
  showProject,
  choices,
  onOpen,
  onPlan,
}: {
  sections: BacklogSection[];
  today: string;
  projectOf: (task: Task) => { name: string; color: string } | null;
  showProject: boolean;
  choices: (task: Task) => Array<{ value: string; label: string }>;
  onOpen: (task: Task) => void;
  onPlan: (task: Task, sprintId: number | null) => void;
}) {
  return (
    <div className="tk-backlog">
      {sections.map((section) => (
        <section className="tk-bl" key={section.key} aria-label={section.title}>
          <div className="tk-bl-head">
            <div className="tk-bl-title">
              <span className="tk-bl-name">{section.title}</span>
              <span className="tk-bl-count">{section.tasks.length}</span>
            </div>
            <div className="tk-bl-sub">{section.sub}</div>
          </div>
          {section.tasks.map((task) => (
            <div
              className="tk-bl-row"
              key={task.id}
              role="button"
              tabIndex={0}
              onClick={() => onOpen(task)}
              onKeyDown={(event) => {
                if (event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) {
                  event.preventDefault();
                  onOpen(task);
                }
              }}
            >
              <div className="tk-bl-main">
                <div className="tk-bl-task">{task.title || "Untitled task"}</div>
                <div className="tk-source">
                  <Icon name="video" size={12} />
                  <span>{sourceLabel(task)}</span>
                </div>
                <TaskBadges task={task} project={projectOf(task)} showProject={showProject} />
              </div>
              <span className={`tk-bl-due${task.status !== "done" && isOverdue(task.due_date, today) ? " tk-due-overdue" : task.due_date === today ? " tk-due-today" : ""}`}>{taskDueText(task, today)}</span>
              <div>
                {task.assignee ? (
                  <div className={`avatar tk-avatar ${avatarTone(task.assignee.user_id)}`} title={task.assignee.name}>
                    {personInitials(task.assignee)}
                  </div>
                ) : (
                  <div className="tk-bl-noowner" title="Needs owner" />
                )}
              </div>
              <select
                className="select tk-bl-select"
                aria-label={`Sprint of ${task.title || "Untitled task"}`}
                value={task.sprint_id === null ? "" : String(task.sprint_id)}
                disabled={!task.can_edit}
                onClick={(event) => event.stopPropagation()}
                onChange={(event) => onPlan(task, event.target.value ? Number(event.target.value) : null)}
              >
                {choices(task).map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          ))}
          {section.tasks.length === 0 ? <div className="tk-bl-empty">{section.emptyText}</div> : null}
        </section>
      ))}
    </div>
  );
}
