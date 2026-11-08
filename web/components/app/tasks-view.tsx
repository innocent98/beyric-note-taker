"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { BacklogView } from "@/components/app/backlog-view";
import { SprintHeader } from "@/components/app/sprint-header";
import { SuggestedTasks } from "@/components/app/suggested-tasks";
import { TaskCard } from "@/components/app/task-card";
import { TaskDrawer } from "@/components/app/task-drawer";
import { INVITE_NAME_KEY } from "@/components/app/team-view";
import { useSuggestedActions } from "@/components/app/use-suggested";
import { useToday } from "@/components/app/use-today";
import { Alert, Button, Icon, PageHead, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { Milestone, Project, Task, TaskList, TaskStatus } from "@/lib/api/types";
import {
  boardSprintIds,
  listFor,
  loadSprintBook,
  sprintChoices,
  sprintNote,
  sprintOption,
  sprintRange,
  sprintTiming,
  type SprintBook,
} from "@/lib/sprints";
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
// W9 adds the sprint selector (Board only), the Board and Backlog tabs, the sprint card with its
// progress and Close sprint, "Carried over" on cards, and the Backlog view with a Sprint select per
// row. Tasks are all loaded once; the sprint chosen, the project tab and the person chips only
// choose which of them are drawn (`sprints` says which sprint each task is in).
// `projects` is null when the team's projects could not be read: the tabs and the project rows are
// then left out rather than drawn empty. `sprints` is null when the sprints could not be read: the
// board then shows every task, as it did before sprints, with a line and Try again; left out
// (undefined) it is simply not drawn.
//
// Suggested tasks: `suggested` is the action items the AI was unsure of (`GET /api/tasks?suggested=true`),
// which the board and every count leave out. Its section sits above the board, hidden when there are
// none; Accept puts a task on the board, Dismiss deletes it, Edit opens the drawer (the task stays
// suggested until it is accepted). `null` (the list could not be read) draws no section.
export function TasksView({
  initial,
  suggested = [],
  projects = null,
  milestones = [],
  sprints,
  canInviteToTeam = false,
}: {
  initial: TaskList | null;
  suggested?: Task[] | null;
  projects?: Project[] | null;
  milestones?: Milestone[];
  sprints?: SprintBook | null;
  canInviteToTeam?: boolean;
}) {
  const today = useToday();
  const router = useRouter();
  const [data, setData] = useState<TaskList | null>(initial);
  const [waiting, setWaiting] = useState<Task[]>(suggested ?? []);
  const [failed, setFailed] = useState(initial === null);
  const [filter, setFilter] = useState<Filter>("all");
  const [groupBy, setGroupBy] = useState<GroupBy>("assigned");
  // "all" or a project id; and whether the board shows only the open milestone's tasks.
  const [projectTab, setProjectTab] = useState<"all" | number>("all");
  const [launchOnly, setLaunchOnly] = useState(false);
  const [book, setBook] = useState<SprintBook | null | undefined>(sprints);
  const [view, setView] = useState<"board" | "backlog">("board");
  const [sprintSel, setSprintSel] = useState<number | null>(null);
  const [closing, setClosing] = useState(false);
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
    setWaiting((current) => current.map((t) => (t.id === next.id ? next : t)));
  }, []);
  const drop = useCallback((id: number) => {
    setData((current) => (current ? { ...current, tasks: current.tasks.filter((t) => t.id !== id) } : current));
    setWaiting((current) => current.filter((t) => t.id !== id));
    setOpenId(null);
  }, []);
  // A task accepted joins the board at once (the answer is the whole task) and leaves the section.
  const accepted = useCallback((next: Task) => {
    setWaiting((current) => current.filter((t) => t.id !== next.id));
    setData((current) =>
      current ? { ...current, tasks: [next, ...current.tasks.filter((t) => t.id !== next.id)] } : current,
    );
  }, []);
  const suggestions = useSuggestedActions({ flash, onAccepted: accepted, onDismissed: drop });

  // The suggestions are read again with the board; a failure here leaves the ones on screen.
  const reloadSuggested = useCallback(async () => {
    try {
      setWaiting((await api.tasks.list({ suggested: true, limit: 500 })).tasks);
    } catch {
      // the section keeps what it had
    }
  }, []);

  async function reload() {
    setFailed(false);
    try {
      setData(await api.tasks.list({ limit: 500 }));
      void reloadSuggested();
    } catch {
      setFailed(true);
    }
  }

  // Tasks and sprints read again: after Close sprint, when the tab comes back (the hourly job may have
  // closed a sprint while it was away) and from Try again. A failure while it was away says nothing.
  const refresh = useCallback(
    async (quiet: boolean) => {
      try {
        const [list, next] = await Promise.all([api.tasks.list({ limit: 500 }), loadSprintBook(api, projects)]);
        setData(list);
        setFailed(false);
        void reloadSuggested();
        if (next || !quiet) setBook(next);
      } catch (caught) {
        if (!quiet) throw caught;
      }
    },
    [projects, reloadSuggested],
  );
  const hasBook = Boolean(book);
  useEffect(() => {
    if (!hasBook) return;
    const back = () => {
      if (document.visibilityState === "visible") void refresh(true);
    };
    document.addEventListener("visibilitychange", back);
    return () => document.removeEventListener("visibilitychange", back);
  }, [hasBook, refresh]);

  // The list of sprints the open project tab is planned in, and the sprint shown on the board.
  const sprintList = book ? listFor(book, projectTab === "all" ? null : projectTab) : null;
  const sprint = sprintList
    ? (sprintList.sprints.find((x) => x.id === sprintSel) ?? sprintList.sprints.find((x) => x.id === sprintList.current_id) ?? null)
    : null;

  async function newTask() {
    if (creating) return;
    setCreating(true);
    try {
      // A new task goes under the project tab that is open (none on "All projects") and, with
      // sprints, into the sprint the Board shows (the Backlog's New task, and a closed sprint, mean
      // the Backlog: an explicit null, not the due-date rule).
      const where = projectTab === "all" ? {} : { project_id: projectTab };
      const made = await api.tasks.create(
        book ? { ...where, sprint_id: view === "board" && sprint && sprint.state !== "closed" ? sprint.id : null } : where,
      );
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

  async function plan(task: Task, sprintId: number | null) {
    try {
      replace(await api.tasks.update(task.id, { sprint_id: sprintId }));
    } catch (caught) {
      flash(errorMessage(caught));
    }
  }

  async function closeSprint() {
    if (!sprint || closing) return;
    setClosing(true);
    try {
      const result = await api.sprints.close(sprint.id);
      setSprintSel(result.current.id);
      await refresh(false);
      flash(
        `${result.closed.name} closed. ${result.moved} unfinished ${result.moved === 1 ? "task" : "tasks"} moved to ${result.current.name}`,
      );
    } catch (caught) {
      // Already closed by the hourly job (or by someone else): read the sprints again, no alarm.
      if (caught instanceof ApiError && caught.code === "sprint_not_current") await refresh(true);
      else flash(errorMessage(caught));
    }
    setClosing(false);
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
  const inProject = tasks.filter((t) => projectTab === "all" || t.project_id === projectTab);
  const filtered = inProject
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
  // Which tasks the sprint shown holds: with every project on show, the current sprint stands for the
  // team's and for each project's own; a closed sprint is only itself. Without sprints, every task.
  const sprintIds = book && sprintList && sprint ? boardSprintIds(book, sprintList, sprint.id, projectTab === "all") : null;
  const inSprint = (t: Task) => (sprintIds ? t.sprint_id !== null && sprintIds.includes(t.sprint_id) : true);
  const shown = filtered.filter(inSprint);
  const inThisSprint = inProject.filter(inSprint);
  const sprintDone = inThisSprint.filter((t) => t.status === "done").length;
  const nextSprint = sprintList?.sprints.find((x) => x.id === sprintList.next_id) ?? null;
  const nextIds = book && sprintList && nextSprint ? boardSprintIds(book, sprintList, nextSprint.id, projectTab === "all") : [];
  const sections =
    sprintList && nextSprint
      ? [
          {
            key: "next",
            title: nextSprint.name,
            sub: `Next sprint, ${sprintRange(nextSprint, today)}. Tasks due after this sprint land here.`,
            emptyText: "Nothing planned yet.",
            tasks: filtered.filter((t) => t.sprint_id !== null && nextIds.includes(t.sprint_id) && t.status !== "done"),
          },
          {
            key: "backlog",
            title: "Backlog",
            sub: "Not scheduled yet. Tasks with no due date land here.",
            emptyText: "The backlog is empty.",
            tasks: filtered.filter((t) => t.sprint_id === null && t.status !== "done"),
          },
        ]
      : [];
  const open = tasks.find((t) => t.id === openId) ?? waiting.find((t) => t.id === openId) ?? null;
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
      <div className="tk-top">
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
                  setSprintSel(null);
                }}
              >
                {p.id === "all" ? null : <span className={`tk-tab-dot ${swatchClass(p.color)}`} />}
                {p.name || "Untitled project"}
              </button>
            ))}
          </div>
        ) : null}
        {book && sprintList && sprint ? (
          <div className="tk-views">
            {view === "board" ? (
              <select
                className="select tk-sprint-select"
                aria-label="Sprint"
                value={sprint.id}
                onChange={(event) => setSprintSel(Number(event.target.value))}
              >
                {sprintList.sprints.map((x) => (
                  <option key={x.id} value={x.id}>
                    {sprintOption(x)}
                  </option>
                ))}
              </select>
            ) : null}
            <div className="tk-tabs" role="group" aria-label="View">
              {(
                [
                  ["board", "Board"],
                  ["backlog", "Backlog"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  className={`tk-tab${view === value ? " tk-tab-on" : ""}`}
                  aria-pressed={view === value}
                  onClick={() => setView(value)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
      {book === null ? (
        <Alert kind="error">
          <span>Could not load sprints. </span>
          <button type="button" className="btn btn-link" onClick={() => void refresh(false).catch(() => setBook(null))}>
            Try again
          </button>
        </Alert>
      ) : null}
      {(book && sprintList && sprint) || milestone ? (
        <div className="tk-cards">
          {book && sprintList && sprint ? (
            <SprintHeader
              sprint={sprint}
              range={sprintRange(sprint, today)}
              timing={sprintTiming(sprint, today)}
              note={sprintNote(sprintList)}
              done={sprintDone}
              total={inThisSprint.length}
              canClose={sprint.state === "current" && sprintList.can_manage}
              closing={closing}
              onClose={() => void closeSprint()}
            />
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
      {suggested !== null ? (
        <SuggestedTasks
          tasks={waiting}
          busyId={suggestions.busyId}
          onAccept={(task) => void suggestions.accept(task)}
          onDismiss={(task) => void suggestions.dismiss(task)}
          onEdit={(task) => setOpenId(task.id)}
        />
      ) : null}
      {view === "board" || !book ? (
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
      ) : (
        <BacklogView
          sections={sections}
          today={today}
          projectOf={(task) => {
            const found = projectOf(task);
            return found && { name: found.name, color: found.color };
          }}
          showProject={projects !== null && projectTab === "all"}
          choices={(task) => sprintChoices(book, task)}
          onOpen={(task) => setOpenId(task.id)}
          onPlan={(task, sprintId) => void plan(task, sprintId)}
        />
      )}
      {open ? (
        <TaskDrawer
          task={open}
          people={people}
          today={today}
          projects={projects}
          milestones={milestones}
          sprints={book ?? null}
          canInvite={canInviteToTeam}
          onAccept={(task) => void suggestions.accept(task)}
          onDismiss={(task) => void suggestions.dismiss(task)}
          suggestionBusy={suggestions.busyId !== null}
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
