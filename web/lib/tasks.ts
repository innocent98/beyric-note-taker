import { PROJECT_COLOURS } from "./brand";
import { initials } from "./format";
import type { Task, TaskPerson, TaskStatus } from "./api/types";

// Helpers for the Tasks screen, the task drawer and the meeting page's Action items. Dates are
// plain `YYYY-MM-DD` strings (the API sends `due_date` and `assigned_on` with no time or zone), so
// they compare as strings; "today" is read in the person's own zone.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export const COLUMNS: ReadonlyArray<readonly [TaskStatus, string]> = [
  ["todo", "To do"],
  ["doing", "In progress"],
  ["done", "Done"],
];
export const AVATAR_TONES = ["avatar-a", "avatar-b", "avatar-c", "avatar-d", "avatar-e"] as const;

// Today's date (YYYY-MM-DD) in `timeZone` (the browser's when null).
export function todayIn(timeZone: string | null, now: Date = new Date()): string {
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone: timeZone ?? undefined }).format(now);
  } catch {
    return now.toISOString().slice(0, 10);
  }
}

function parts(iso: string): { y: number; m: number; d: number; weekday: number } {
  const [y, m, d] = iso.split("-").map(Number);
  return { y, m, d, weekday: new Date(Date.UTC(y, m - 1, d)).getUTCDay() };
}

export function addDays(iso: string, days: number): string {
  const { y, m, d } = parts(iso);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

// "Fri 6 Nov": how the designer writes a day.
export function dayLabel(iso: string): string {
  const { m, d, weekday } = parts(iso);
  return `${DAYS[weekday]} ${d} ${MONTHS[m - 1]}`;
}

// A due day before today. The designer's file draws it in red and says "Overdue" (a finished task
// is never overdue: it keeps its plain grey day).
export function isOverdue(due: string | null, today: string): boolean {
  return due !== null && due !== "" && due < today;
}

// "Today", "Tomorrow", "Fri 6 Nov", or "No due date".
export function dueLabel(due: string | null, today: string): string {
  if (!due) return "No due date";
  if (due === today) return "Today";
  if (due === addDays(today, 1)) return "Tomorrow";
  return dayLabel(due);
}

// The due line on a task card, a Backlog row, a series row and a meeting's action item: the
// day, or "Overdue, Fri 30 Oct" while the task is open and the day has passed.
export function taskDueText(task: Pick<Task, "due_date" | "status">, today: string): string {
  return task.status !== "done" && isOverdue(task.due_date, today)
    ? `Overdue, ${dayLabel(task.due_date as string)}`
    : dueLabel(task.due_date, today);
}

// "Today, Mon 2 Nov" or "Fri 30 Oct": the day a task was assigned.
export function assignedLabel(day: string | null, today: string): string {
  if (!day) return "";
  return day === today ? `Today, ${dayLabel(day)}` : dayLabel(day);
}

// The options of the Due select: No due date, Today, Tomorrow and the days after, as the
// designer's list does, plus the task's own date when it is outside them.
export function dueOptions(today: string, current: string | null): Array<{ value: string; label: string }> {
  const days = Array.from({ length: 8 }, (_, i) => addDays(today, i));
  if (current && !days.includes(current)) days.push(current);
  days.sort();
  return [
    { value: "", label: "No due date" },
    ...days.map((day) => ({ value: day, label: dueLabel(day, today) })),
  ];
}

export type GroupBy = "assigned" | "due";

export function groupLabel(by: GroupBy, key: string, today: string): string {
  if (by === "due") {
    if (key === "") return "No due date";
    if (key === today) return "Due today";
    return isOverdue(key, today) ? `Overdue, was due ${dayLabel(key)}` : `Due ${dueLabel(key, today)}`;
  }
  return `Assigned ${assignedLabel(key, today)}`;
}

// Tasks of one column in groups: by the day they were assigned (newest first) or by due date
// (earliest first, no date last).
export function groupTasks(list: Task[], by: GroupBy, today: string): Array<{ key: string; label: string; tasks: Task[] }> {
  const keyOf = (t: Task) => (by === "due" ? (t.due_date ?? "") : (t.assigned_on ?? today));
  const keys = [...new Set(list.map(keyOf))];
  keys.sort((a, b) => {
    if (by === "due") return a === "" ? 1 : b === "" ? -1 : a < b ? -1 : 1;
    return a < b ? 1 : -1;
  });
  return keys.map((key) => ({ key, label: groupLabel(by, key, today), tasks: list.filter((t) => keyOf(t) === key) }));
}

// Where a task came from, as the card writes it: the meeting's title and day, "Added manually",
// or (a task from a meeting its holder may not read) just that it came from one.
export function sourceLabel(task: Task): string {
  if (task.meeting) {
    const { m, d } = parts(task.meeting.created_at.slice(0, 10));
    return `${task.meeting.title}, ${d} ${MONTHS[m - 1]}`;
  }
  return task.from_meeting ? "From a meeting" : "Added manually";
}

export function avatarTone(personId: number): string {
  return AVATAR_TONES[personId % AVATAR_TONES.length];
}

export function personInitials(person: Pick<TaskPerson, "name">): string {
  return initials(person.name);
}

// "3 tasks created, assigned to 2 people, 1 to confirm": the line above the meeting page's task
// rows. A suggested task is not created yet, so it is counted only in the tail (shown when above 0),
// and its person is not counted: the same numbers `GET /api/meetings/{id}/tasks` gives
// (`assigned_people`, `to_confirm`).
export function actionSummary(tasks: Task[]): string {
  const made = tasks.filter((t) => !t.suggested);
  const people = new Set(made.filter((t) => t.assignee).map((t) => t.assignee!.user_id)).size;
  const waiting = tasks.length - made.length;
  const t = made.length;
  return (
    `${t} ${t === 1 ? "task" : "tasks"} created, assigned to ${people} ${people === 1 ? "person" : "people"}` +
    (waiting > 0 ? `, ${waiting} to confirm` : "")
  );
}

// The confidence pill of a suggested task: "Medium confidence" (the designer's CONF table; the class
// that paints it is `tk-conf-medium`, `-low` or `-high` in styles/pages.css). A manual task and an
// older one have none.
export function confidenceLabel(confidence: Task["confidence"]): string {
  return confidence ? `${confidence[0].toUpperCase()}${confidence.slice(1)} confidence` : "";
}

// The drawer's amber box opens with "Suggested task, medium confidence." (the designer's `confLower`).
export function suggestedLead(task: Pick<Task, "confidence">): string {
  return task.confidence ? `Suggested task, ${confidenceLabel(task.confidence).toLowerCase()}.` : "Suggested task.";
}

// Where "Show source" and "Show in transcript" go: the meeting's Transcript tab at the row that
// holds the quote (the page `ask-screens` builds highlights it; until then it just opens the tab).
// Null when the viewer may not open the meeting.
export function transcriptHref(task: Pick<Task, "meeting" | "quote_line">): string | null {
  if (!task.meeting) return null;
  const line = task.quote_line ? `&line=${task.quote_line}` : "";
  return `/meetings/${task.meeting.id}?tab=transcript${line}`;
}

// ---- projects and milestones (W4) ----

// "Tue 8 Dec 2026": a milestone's date on its card and on the project.
export function longDay(iso: string): string {
  return `${dayLabel(iso)} ${parts(iso).y}`;
}

// "8 Dec": a milestone's date in a select and on the meeting page.
export function shortDay(iso: string): string {
  const { m, d } = parts(iso);
  return `${d} ${MONTHS[m - 1]}`;
}

// "Client Portal launch, 8 Dec": how a milestone is named in a select and on the meeting page.
export function milestoneTitle(m: { name: string; due_date: string }): string {
  return `${m.name}, ${shortDay(m.due_date)}`;
}

// Whole days from `today` to `due` (never below 0: a milestone that has passed is not on the board).
export function daysLeft(due: string, today: string): number {
  const a = parts(today);
  const b = parts(due);
  return Math.max(0, Math.round((Date.UTC(b.y, b.m - 1, b.d) - Date.UTC(a.y, a.m - 1, a.d)) / 86_400_000));
}

// The class that paints a project's colour (styles/pages.css `sw-1` to `sw-6`): the CSP forbids
// inline styles, so a colour is always a class. A colour that is not one of the six is the grey.
export function swatchClass(color: string | null | undefined): string {
  const index = PROJECT_COLOURS.findIndex((c) => c.toLowerCase() === (color ?? "").toLowerCase());
  return index < 0 ? "sw-none" : `sw-${index + 1}`;
}
