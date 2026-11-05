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

// "Today", "Tomorrow", "Fri 6 Nov", or "No due date".
export function dueLabel(due: string | null, today: string): string {
  if (!due) return "No due date";
  if (due === today) return "Today";
  if (due === addDays(today, 1)) return "Tomorrow";
  return dayLabel(due);
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
  if (by === "due") return key === "" ? "No due date" : key === today ? "Due today" : `Due ${dueLabel(key, today)}`;
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

// "3 tasks created, assigned to 2 people": the line above the meeting page's task rows.
export function actionSummary(tasks: Task[]): string {
  const people = new Set(tasks.filter((t) => t.assignee).map((t) => t.assignee!.user_id)).size;
  const t = tasks.length;
  return `${t} ${t === 1 ? "task" : "tasks"} created, assigned to ${people} ${people === 1 ? "person" : "people"}`;
}
