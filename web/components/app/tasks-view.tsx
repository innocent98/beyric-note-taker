"use client";

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { TaskCard } from "@/components/app/task-card";
import { TaskDrawer } from "@/components/app/task-drawer";
import { INVITE_NAME_KEY } from "@/components/app/team-view";
import { useToday } from "@/components/app/use-today";
import { Alert, Button, Icon, PageHead, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { Milestone, Project, Task, TaskList, TaskStatus } from "@/lib/api/types";
import { COLUMNS, daysLeft, type GroupBy, groupTasks, longDay, shortDay, swatchClass } from "@/lib/tasks";

// "all", "me", "none" (Needs owner), or `u<id>`: one teammate (the designer's chips: All tasks, My
// tasks, one per active teammate by first name, Needs owner).
type Filter = string;
const GROUPS: ReadonlyArray<readonly [GroupBy, string]> = [
  ["assigned", "Date assigned"],
  ["due", "Due date"],
];
const COLUMN_DOT: Record<TaskStatus, string> = { todo: "tk-dot-todo", doing: "tk-dot-doing", done: "tk-dot-done" };

// The Tasks screen (the designer's `isTasks`): filter chips, Group by, the attention banner and the
// board of three columns with drag and drop, and the task drawer. `initial` is the server render
// (null when it failed: the page then says so and offers Try again). Every change goes to the API
// at once and the answer replaces the task on screen; a drop that is refused puts the card back.
//
// W4 adds the project tabs (All projects and one per project), the milestone card with "Show launch
// tasks", the project badge and "Check project" on cards, and the attention banner's project line.
// Left out because what they need does not exist yet, and no control is drawn for them: the sprint
// selector and header, the Board and Backlog tabs and "Carried over" (W9; docs/sop/2026-11-05-w2-tasks-ui.md).
// `projects` is null when the team's projects could not be read: the tabs and the project rows are
// then left out rather than drawn empty.
export function TasksView({
  initial,
  projects = null,
  milestones = [],
  canInviteToTeam = false,
}: {
  initial: TaskList | null;
  projects?: Project[] | null;
  milestones?: Milestone[];
  canInviteToTeam?: boolean;
}) {
  const today = useToday();
  const router = useRouter();
  const [data, setData] = useState<TaskList | null>(initial);
  const [failed, setFailed] = useState(initial === null);
  const [filter, setFilter] = useState<Filter>("all");
  const [groupBy, setGroupBy] = useState<GroupBy>("assigned");
  // "all" or a project id; and whether the board shows only the open milestone's tasks.
  const [projectTab, setProjectTab] = useState<"all" | number>("all");
  const [launchOnly, setLaunchOnly] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const [dragId, setDragId] = useState<number | null>(null);
  const [overColumn, setOverColumn] = useState<TaskStatus | null>(null);
  const [creating, setCreating] = useState(false);
  const [toast, flash] = useToast();
  const dragging = useRef<number | null>(null);

  const tasks = data?.tasks ?? [];
  const people = data?.people ?? [];

  const replace = useCallback((next: Task) => {
    setData((current) =>
      current ? { ...current, tasks: current.tasks.map((t) => (t.id === next.id ? next : t)) } : current,
    );
  }, []);
  const drop = useCallback((id: number) => {
    setData((current) => (current ? { ...current, tasks: current.tasks.filter((t) => t.id !== id) } : current));
    setOpenId(null);
  }, []);

  async function reload() {
    setFailed(false);
    try {
      setData(await api.tasks.list({ limit: 500 }));
    } catch {
      setFailed(true);
    }
  }

  async function newTask() {
    if (creating) return;
    setCreating(true);
    try {
      // A new task goes under the project tab that is open (none on "All projects").
      const made = await api.tasks.create(projectTab === "all" ? {} : { project_id: projectTab });
      setData((current) => (current ? { ...current, tasks: [made, ...current.tasks] } : current));
      setOpenId(made.id);
    } catch (caught) {
      flash(errorMessage(caught));
    }
    setCreating(false);
  }

  async function move(id: number, status: TaskStatus) {
    const task = tasks.find((t) => t.id === id);
    if (!task || task.status === status || !task.can_update) return;
    replace({ ...task, status, is_new: false });
    try {
      replace(await api.tasks.update(id, { status }));
    } catch (caught) {
      replace(task);
      flash(errorMessage(caught));
    }
  }

  const chips: Array<readonly [Filter, string]> = [
    ["all", "All tasks"],
    ["me", "My tasks"],
    ...people.filter((p) => !p.is_you).map((p) => [`u${p.user_id}`, p.name.split(" ")[0]] as const),
    ["none", "Needs owner"],
  ];
  const projectList = projects ?? [];
  const projectOf = (t: Task) => projectList.find((p) => p.id === t.project_id) ?? null;
  // The milestone card: the next confirmed milestone not yet past, within the open project tab.
  const milestone =
    milestones
      .filter((m) => m.due_date >= today && (projectTab === "all" || m.project_id === projectTab))
      .sort((a, b) => (a.due_date < b.due_date ? -1 : a.due_date > b.due_date ? 1 : a.id - b.id))[0] ?? null;
  const launch = milestone && launchOnly ? milestone : null;
  const shown = tasks
    .filter((t) => projectTab === "all" || t.project_id === projectTab)
    .filter((t) => (launch ? t.milestone_id === launch.id : true))
    .filter((t) =>
    filter === "all"
      ? true
      : filter === "none"
        ? !t.assignee
        : filter === "me"
          ? t.assignee?.is_you === true
          : `u${t.assignee?.user_id}` === filter,
  );
  const open = tasks.find((t) => t.id === openId) ?? null;
  const needOwner = tasks.filter((t) => !t.assignee && t.status !== "done");
  const needCheck = tasks.filter((t) => t.check_project && t.status !== "done");
  const firstToReview = needOwner[0] ?? needCheck[0];
  const attention = needOwner.length + needCheck.length;
  const parts = [
    ...(needOwner.length ? [needOwner.length === 1 ? "1 needs an owner" : `${needOwner.length} need an owner`] : []),
    ...(needCheck.length
      ? [needCheck.length === 1 ? "1 needs its project checked" : `${needCheck.length} need their project checked`]
      : []),
  ];
  const launchTasks = milestone ? tasks.filter((t) => t.milestone_id === milestone.id) : [];
  const launchDone = launchTasks.filter((t) => t.status === "done").length;
  const launchPct = launchTasks.length ? Math.round((launchDone / launchTasks.length) * 100) : 0;

  if (failed || data === null) {
    return (
      <div className="stack-lg">
        <PageHead title="Tasks" subtitle="Action items from your meetings, assigned to the people responsible." />
        <Alert kind="error">
          <span>Could not load tasks. </span>
          <button type="button" className="btn btn-link" onClick={() => void reload()}>
            Try again
          </button>
        </Alert>
      </div>
    );
  }

  return (
    <div className="stack-lg tk">
      <PageHead title="Tasks" subtitle="Action items from your meetings, assigned to the people responsible.">
        <Button type="button" size="md" onClick={() => void newTask()} disabled={creating}>
          <Icon name="plus" size={14} stroke={2.4} />
          New task
        </Button>
      </PageHead>
      {projects && projectList.length > 0 ? (
        <div className="tk-tabs" role="group" aria-label="Project">
          {([{ id: "all" as const, name: "All projects", color: null }, ...projectList]).map((p) => (
            <button
              key={p.id}
              type="button"
              className={`tk-tab${projectTab === p.id ? " tk-tab-on" : ""}`}
              aria-pressed={projectTab === p.id}
              onClick={() => {
                setProjectTab(p.id);
                setLaunchOnly(false);
              }}
            >
              {p.id === "all" ? null : <span className={`tk-tab-dot ${swatchClass(p.color)}`} />}
              {p.name || "Untitled project"}
            </button>
          ))}
        </div>
      ) : null}
      {milestone ? (
        <div className="tk-milestone">
          <div className="tk-ms-days">
            <div className="tk-ms-days-n">{daysLeft(milestone.due_date, today)}</div>
            <div className="tk-ms-days-l">days left</div>
          </div>
          <div className="tk-ms-main">
            <div className="tk-ms-title">
              <span className="tk-ms-name">{milestone.name}</span>
              <span className="tk-ms-date">{longDay(milestone.due_date)}</span>
            </div>
            <progress
              className="tk-ms-bar"
              max={100}
              value={launchPct}
              aria-label={`${launchDone} of ${launchTasks.length} launch tasks done`}
            />
            <div className="tk-ms-progress">
              {launchDone} of {launchTasks.length} launch tasks done
              {milestone.source_meeting
                ? ` · Set in ${milestone.source_meeting.title}, ${shortDay(milestone.source_meeting.created_at.slice(0, 10))}`
                : ""}
            </div>
          </div>
          <button type="button" className="tk-ms-filter" aria-pressed={launchOnly} onClick={() => setLaunchOnly((v) => !v)}>
            {launchOnly ? "Show all tasks" : "Show launch tasks"}
          </button>
        </div>
      ) : null}
      <div className="tk-bar">
        <div className="tk-chips" role="group" aria-label="Filter tasks">
          {chips.map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={`tk-chip${filter === value ? " tk-chip-on" : ""}`}
              aria-pressed={filter === value}
              onClick={() => setFilter(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="tk-group">
          Group by
          <div className="tk-seg" role="group" aria-label="Group by">
            {GROUPS.map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={`tk-seg-opt${groupBy === value ? " tk-seg-on" : ""}`}
                aria-pressed={groupBy === value}
                onClick={() => setGroupBy(value)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>
      {attention > 0 ? (
        <div className="tk-attention" role="status">
          <div className="tk-attention-text">
            <div className="tk-attention-title">
              {attention === 1 ? "1 task needs attention" : `${attention} tasks need attention`}: {parts.join(", ")}
            </div>
            <div className="tk-attention-sub">
              Owners and projects are filled in from the meeting. These ones could not be matched with confidence.
            </div>
          </div>
          <button type="button" className="tk-attention-btn" onClick={() => firstToReview && setOpenId(firstToReview.id)}>
            Review
          </button>
        </div>
      ) : null}
        <div className="tk-board">
          {COLUMNS.map(([status, label]) => {
            const list = shown.filter((t) => t.status === status);
            return (
              <section
                key={status}
                aria-label={label}
                className={`tk-col${overColumn === status ? " tk-col-over" : ""}`}
                onDragOver={(event) => {
                  event.preventDefault();
                  if (overColumn !== status) setOverColumn(status);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  const id = dragging.current;
                  dragging.current = null;
                  setOverColumn(null);
                  setDragId(null);
                  if (id !== null) void move(id, status);
                }}
              >
                <div className="tk-col-head">
                  <span className={`tk-dot ${COLUMN_DOT[status]}`} />
                  <span className="tk-col-label">{label}</span>
                  <span className="tk-col-count">{list.length}</span>
                </div>
                {groupTasks(list, groupBy, today).map((group) => (
                  <div className="tk-group-block" key={group.key}>
                    <div className="tk-group-label">
                      <span>{group.label}</span>
                      <span className="tk-group-rule" />
                    </div>
                    {group.tasks.map((task) => (
                      <TaskCard
                        key={task.id}
                        task={task}
                        today={today}
                        project={projectOf(task) && { name: projectOf(task)!.name, color: projectOf(task)!.color }}
                        showProject={projects !== null && projectTab === "all"}
                        dragging={dragId === task.id}
                        onOpen={() => setOpenId(task.id)}
                        onDragStart={(event) => {
                          dragging.current = task.id;
                          setDragId(task.id);
                          try {
                            event.dataTransfer.effectAllowed = "move";
                            event.dataTransfer.setData("text/plain", String(task.id));
                          } catch {
                            // some browsers refuse; the drag still works from `dragging`
                          }
                        }}
                        onDragEnd={() => {
                          setDragId(null);
                          setOverColumn(null);
                        }}
                      />
                    ))}
                  </div>
                ))}
                {list.length === 0 ? <div className="tk-empty">Drag tasks here</div> : null}
              </section>
            );
          })}
        </div>
      {open ? (
        <TaskDrawer
          task={open}
          people={people}
          today={today}
          projects={projects}
          milestones={milestones}
          canInvite={canInviteToTeam}
          onInvite={(name) => {
            // The Team page opens its invite dialog with this name (team-view.tsx).
            try {
              window.sessionStorage.setItem(INVITE_NAME_KEY, name);
            } catch {
              // storage off: the dialog is one click away on the Team page
            }
            router.push("/team");
          }}
          onChange={replace}
          onDelete={drop}
          onClose={() => setOpenId(null)}
        />
      ) : null}
      <Toast message={toast} />
    </div>
  );
}
