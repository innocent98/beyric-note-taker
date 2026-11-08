import type { Decision, DecisionCounts, DecisionStatus } from "@/lib/api/types";
import { addDays, longDay } from "@/lib/tasks";

// The Decisions record's words and rules, in one place (docs/fe-integration-guide-decisions.md).

// The designer's `DSTATUS` labels, in the order of its Status select.
export const STATUSES: ReadonlyArray<readonly [DecisionStatus, string]> = [
  ["approved", "Approved"],
  ["proposed", "Proposed"],
  ["replaced", "Replaced"],
  ["rejected", "Rejected"],
];

export const TABS: ReadonlyArray<readonly [DecisionStatus | "all", string]> = [["all", "All"], ...STATUSES];

export function statusLabel(status: DecisionStatus): string {
  return STATUSES.find(([value]) => value === status)?.[1] ?? "Approved";
}

// Replaced and rejected decisions are drawn greyed out.
export function isSetAside(status: DecisionStatus): boolean {
  return status === "replaced" || status === "rejected";
}

// "Decided by Kemi Adeyemi · 2 related tasks · Revisit Mon 30 Nov 2026": each part only when there is one.
export function metaLine(decision: Pick<Decision, "decided_by" | "related_tasks" | "revisit_on">): string {
  const parts: string[] = [];
  if (decision.decided_by) parts.push(`Decided by ${decision.decided_by}`);
  const tasks = decision.related_tasks.length;
  if (tasks > 0) parts.push(`${tasks} related ${tasks === 1 ? "task" : "tasks"}`);
  if (decision.revisit_on) parts.push(`Revisit ${longDay(decision.revisit_on)}`);
  return parts.join(" · ");
}

// The banner: "1 decision is due to be revisited this week." / "2 decisions are due ...".
export function revisitBanner(count: number): string {
  return `${count} ${count === 1 ? "decision is" : "decisions are"} due to be revisited this week.`;
}

// The Monday and the Sunday of the week `today` (YYYY-MM-DD) is in: the server counts the banner over
// this week in the person's zone, so the screen can keep the number right after a change.
export function weekOf(today: string): [string, string] {
  const weekday = new Date(`${today}T00:00:00Z`).getUTCDay(); // 0 is Sunday
  const monday = addDays(today, -((weekday + 6) % 7));
  return [monday, addDays(monday, 6)];
}

// Counts toward the banner: approved or proposed, with a revisit day this week.
export function isDueThisWeek(decision: Pick<Decision, "status" | "revisit_on">, today: string): boolean {
  if (!decision.revisit_on || (decision.status !== "approved" && decision.status !== "proposed")) return false;
  const [monday, sunday] = weekOf(today);
  return decision.revisit_on >= monday && decision.revisit_on <= sunday;
}

// The tab counts after one decision changed status.
export function movedCount(counts: DecisionCounts, from: DecisionStatus, to: DecisionStatus): DecisionCounts {
  if (from === to) return counts;
  return { ...counts, [from]: Math.max(0, counts[from] - 1), [to]: counts[to] + 1 };
}

// The evidence box's link (the meeting page highlights that row; no time is shown anywhere).
export function transcriptHref(meetingId: number, line: number): string {
  return `/meetings/${meetingId}?tab=transcript&line=${line}`;
}

// The search that finds one decision again: its words, cut to what the API accepts.
export const SEARCH_MAX = 200;
export function searchFor(text: string): string {
  return text.slice(0, SEARCH_MAX);
}

// A related task's dot and words (the designer's `relatedList`).
export function relatedTone(status: "todo" | "doing" | "done"): { dot: string; label: string } {
  if (status === "done") return { dot: "rel-dot-done", label: "Done" };
  if (status === "doing") return { dot: "rel-dot-doing", label: "In progress" };
  return { dot: "rel-dot-todo", label: "To do" };
}

// A stable colour for a speaker's initials (no person id on a speaker label).
export function toneOf(name: string): number {
  let sum = 0;
  for (const ch of name) sum += ch.charCodeAt(0);
  return sum;
}
