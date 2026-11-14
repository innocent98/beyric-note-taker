import { dayLabel } from "./tasks";
import type { Project, Sprint, SprintLength, SprintList, Task } from "./api/types";

// Helpers for the sprint screens (W9): the Tasks board and Backlog, the task drawer's Sprint row,
// the Settings card and the Projects screen. The API sends dates as plain `YYYY-MM-DD` and the
// state of each sprint (`closed`, `current`, `next`); it does not send labels (docs/fe-integration-
// guide-sprints.md), so the words the designer drew are built here.

// The designer's Sprint length list (`LEN` in Beyric Note Taker.dc.html). A project's select has no
// Custom (the API refuses it there).
export const SPRINT_LENGTHS: ReadonlyArray<readonly [SprintLength, string]> = [
  ["week5", "1 week, 5 working days"],
  ["week7", "1 week, 7 days"],
  ["week14", "2 weeks"],
  ["month1", "1 month, 30 days"],
  ["month3", "3 months"],
  ["month6", "6 months"],
  ["custom", "Custom"],
];

// "1 week, 5 working days", "2 weeks" or (custom) "10 days": the team's length in words, as the
// designer's "Workspace default (...)" option writes it.
export function lengthLabel(length: SprintLength, customDays: number): string {
  if (length === "custom") return `${customDays} days`;
  return SPRINT_LENGTHS.find(([value]) => value === length)?.[1] ?? length;
}

// "Mon 2 Nov to Fri 6 Nov"; a year is added to a day that is not in `today`'s year (the designer's
// `fmtD` adds it for any year but the sample's).
export function sprintRange(sprint: Sprint, today: string): string {
  const year = (iso: string) => (iso.slice(0, 4) === today.slice(0, 4) ? "" : ` ${iso.slice(0, 4)}`);
  return `${dayLabel(sprint.start_date)}${year(sprint.start_date)} to ${dayLabel(sprint.end_date)}${year(sprint.end_date)}`;
}

function daysBetween(from: string, to: string): number {
  const a = from.split("-").map(Number);
  const b = to.split("-").map(Number);
  return Math.round((Date.UTC(b[0], b[1] - 1, b[2]) - Date.UTC(a[0], a[1] - 1, a[2])) / 86_400_000);
}

// The pill beside a sprint's dates (the designer's `timing`): "Closed", "Starts Mon 9 Nov", "Last day"
// or "4 days left".
export function sprintTiming(sprint: Sprint, today: string): string {
  if (sprint.state === "closed" || sprint.end_date < today) return "Closed";
  if (sprint.start_date > today) return `Starts ${dayLabel(sprint.start_date)}`;
  const left = daysBetween(today, sprint.end_date);
  return left === 0 ? "Last day" : `${left} ${left === 1 ? "day" : "days"} left`;
}

// The line under a sprint's progress bar after "x of y done" (the designer's `note`).
export function sprintNote(list: Pick<SprintList, "sprint_length" | "sprint_custom_days" | "sprint_align_weekdays">): string {
  if (list.sprint_align_weekdays && list.sprint_length === "week5") return "Opens at Monday planning, closes at Friday review";
  if (list.sprint_length === "custom") return `${list.sprint_custom_days}-day sprints`;
  return `${lengthLabel(list.sprint_length, list.sprint_custom_days)} sprints`;
}

// "Sprint 12 (current)", "Sprint 11 (closed)" or "Sprint 13 (next)": an option of a sprint select.
export function sprintOption(sprint: Sprint): string {
  return `${sprint.name} (${sprint.state})`;
}

// Everything the board needs about sprints: the team's pair and, for each project that has a sprint
// length of its own, that project's pair (`GET /api/sprints` and `GET /api/sprints?project=<id>`).
export type SprintBook = { team: SprintList; own: Record<number, SprintList> };

type SprintsApi = { sprints: { list: (project?: number, team?: number | null) => Promise<SprintList> } };

// Reads the team's sprints and those of each project with its own length. `null` when any read fails
// (the screens then say so and offer Try again rather than draw half the picture).
// `team`: the team the thing on screen belongs to (the meeting page's drawer), else the tab's.
export async function loadSprintBook(api: SprintsApi, projects: Project[] | null, team?: number | null): Promise<SprintBook | null> {
  try {
    const owned = (projects ?? []).filter((p) => p.sprint_length !== null);
    const [teamList, ...lists] = await Promise.all([api.sprints.list(undefined, team), ...owned.map((p) => api.sprints.list(p.id, team))]);
    return { team: teamList, own: Object.fromEntries(owned.map((p, i) => [p.id, lists[i]])) };
  } catch {
    return null;
  }
}

// The list a project's tasks are planned into: its own when it has one, else the team's.
export function listFor(book: SprintBook, projectId: number | null): SprintList {
  return (projectId !== null && book.own[projectId]) || book.team;
}

function allLists(book: SprintBook): SprintList[] {
  return [book.team, ...Object.values(book.own)];
}

// The ids that stand for "the current sprint" of the board: with every project shown, the team's
// current sprint and the current sprint of each project with its own length; otherwise the one
// sprint chosen. The same for "next". A closed sprint is only itself.
export function boardSprintIds(book: SprintBook, list: SprintList, sprintId: number, allProjects: boolean): number[] {
  if (!allProjects) return [sprintId];
  if (sprintId === list.current_id) return allLists(book).map((l) => l.current_id);
  if (sprintId === list.next_id) return allLists(book).map((l) => l.next_id);
  return [sprintId];
}

// The options of a task's Sprint select (the designer's `sprintOpts`): Backlog, the current and the
// next sprint of the series the task's project is in. A task left in a closed sprint (a done task)
// shows that sprint too, so the select tells the truth about where it is.
export function sprintChoices(book: SprintBook, task: Task): Array<{ value: string; label: string }> {
  const list = listFor(book, task.project_id);
  const open = list.sprints.filter((s) => s.state !== "closed");
  const held = list.sprints.find((s) => s.id === task.sprint_id && s.state === "closed");
  return [
    { value: "", label: "Backlog" },
    ...(held ? [{ value: String(held.id), label: sprintOption(held) }] : []),
    ...open.map((s) => ({ value: String(s.id), label: sprintOption(s) })),
  ];
}
