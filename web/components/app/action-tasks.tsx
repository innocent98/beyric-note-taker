"use client";

import { useState } from "react";
import { TaskDrawer } from "@/components/app/task-drawer";
import { useSuggestedActions } from "@/components/app/use-suggested";
import { useToday } from "@/components/app/use-today";
import { AppLink, Icon, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/browser";
import { loadSprintBook, type SprintBook } from "@/lib/sprints";
import type { Milestone, Project, Task, TaskPerson } from "@/lib/api/types";
import { actionSummary, avatarTone, isOverdue, personInitials, taskDueText } from "@/lib/tasks";

// The meeting page's Action items when the notes have been read into tasks (the designer's
// `hasActionTasks` block): the heading, "N tasks created, assigned to M people · View board", and
// one row per task with a tick box, the title, the due day and who has it (or "Needs owner").
// A row opens the same drawer as the board; the tick box moves the task between To do and Done.
// Titles are plain text. The drawer's people list is read when the first row is opened.
// A suggested task (the AI was unsure of it) has the pill "To confirm" in place of "Needs owner", is
// not counted in "N tasks created, assigned to M people", and is accepted or dismissed from its drawer
// (the summary then ends ", K to confirm" while K is above 0).
// `team` is the team the MEETING belongs to (multi-team): the drawer's people, projects, milestones and
// sprints are that team's, whatever team this tab is working on. undefined: the tab's own, as before.
export function ActionTasks({ initial, team }: { initial: Task[]; team?: number | null }) {
  const today = useToday();
  const [tasks, setTasks] = useState(initial);
  const [people, setPeople] = useState<TaskPerson[]>([]);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [milestones, setMilestones] = useState<Milestone[]>([]);
  const [sprints, setSprints] = useState<SprintBook | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [failed, setFailed] = useState<number | null>(null);
  const [toast, flash] = useToast();
  const suggestions = useSuggestedActions({
    flash,
    onAccepted: (next) => setTasks((all) => all.map((t) => (t.id === next.id ? next : t))),
    onDismissed: (id) => {
      setTasks((all) => all.filter((t) => t.id !== id));
      setOpenId((now) => (now === id ? null : now));
    },
  });

  async function open(id: number) {
    setOpenId(id);
    if (people.length === 0) {
      try {
        setPeople((await api.tasks.list({ limit: 1 }, team)).people);
      } catch {
        // the drawer still opens; the assignee list is just short
      }
      try {
        const read = (await api.projects.list(team)).projects;
        setProjects(read);
        setMilestones((await api.milestones.list(team)).milestones);
        // The Sprint row (W9); left out when the sprints cannot be read.
        setSprints(await loadSprintBook(api, read, team));
      } catch {
        // the Project and Milestone rows are then left out of the drawer
      }
    }
  }

  async function toggle(task: Task) {
    const status = task.status === "done" ? "todo" : "done";
    setFailed(null);
    setTasks((all) => all.map((t) => (t.id === task.id ? { ...t, status, is_new: false } : t)));
    try {
      const next = await api.tasks.update(task.id, { status });
      setTasks((all) => all.map((t) => (t.id === task.id ? next : t)));
    } catch {
      setTasks((all) => all.map((t) => (t.id === task.id ? task : t)));
      setFailed(task.id);
    }
  }

  const current = tasks.find((t) => t.id === openId) ?? null;
  return (
    <>
      <div className="ai-head">
        <h2>Action items</h2>
        <AppLink className="ai-board" href="/tasks">
          {actionSummary(tasks)} · View board
        </AppLink>
      </div>
      <div className="ai-list">
        {tasks.map((task) => {
          const done = task.status === "done";
          return (
            <div
              className="ai-row"
              key={task.id}
              role="button"
              tabIndex={0}
              onClick={() => void open(task.id)}
              onKeyDown={(event) => {
                if (event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) {
                  event.preventDefault();
                  void open(task.id);
                }
              }}
            >
              <button
                type="button"
                className={`ai-box${done ? " ai-box-on" : ""}`}
                role="checkbox"
                aria-checked={done}
                aria-label={`Mark "${task.title || "Untitled task"}" ${done ? "not done" : "done"}`}
                disabled={!task.can_update}
                onClick={(event) => {
                  event.stopPropagation();
                  void toggle(task);
                }}
              >
                {done ? <Icon name="check" size={12} stroke={3.2} /> : null}
              </button>
              <div>
                <div className={`ai-title${done ? " tk-done" : ""}`}>{task.title || "Untitled task"}</div>
                <div className={done || !isOverdue(task.due_date, today) ? "ai-due" : "ai-due tk-due-overdue"}>
                  <Icon name="calendar" size={12} />
                  {taskDueText(task, today)}
                </div>
                {failed === task.id ? <div className="ai-due" role="alert">Could not update this task. Try again.</div> : null}
              </div>
              {task.suggested ? <span className="tk-owner-pill">To confirm</span> : null}
              {task.assignee ? (
                <span className="ai-who">
                  <span className={`avatar ${avatarTone(task.assignee.user_id)}`}>{personInitials(task.assignee)}</span>
                  {task.assignee.name}
                </span>
              ) : task.suggested ? null : (
                <span className="tk-owner-pill">Needs owner</span>
              )}
            </div>
          );
        })}
      </div>
      {current ? (
        <TaskDrawer
          task={current}
          people={people}
          today={today}
          projects={projects}
          milestones={milestones}
          sprints={sprints}
          onAccept={(task) => void suggestions.accept(task)}
          onDismiss={(task) => void suggestions.dismiss(task)}
          suggestionBusy={suggestions.busyId !== null}
          onChange={(next) => setTasks((all) => all.map((t) => (t.id === next.id ? next : t)))}
          onDelete={(id) => {
            setTasks((all) => all.filter((t) => t.id !== id));
            setOpenId(null);
          }}
          onClose={() => setOpenId(null)}
        />
      ) : null}
      <Toast message={toast} />
    </>
  );
}
