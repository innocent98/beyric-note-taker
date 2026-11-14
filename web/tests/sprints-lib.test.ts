import { describe, expect, it, vi } from "vitest";
import type { ProjectList, SprintList, Task, TaskList } from "@/lib/api/types";
import {
  boardSprintIds,
  lengthLabel,
  listFor,
  loadSprintBook,
  sprintChoices,
  sprintNote,
  sprintOption,
  sprintRange,
  sprintTiming,
  type SprintBook,
} from "@/lib/sprints";
import { sprintsBody } from "./helpers";

// The sprint words and rules of the screens, fed the bodies captured from the running API
// (docs/fe-captures/sprints-w9/): 09 the team's first two sprints, 50 a project's own pair, 63 the
// team's sprints after "Close sprint" by hand (Sprint 1 closed early).

const team = sprintsBody<SprintList>("09-sprints-first.json");
const own = sprintsBody<SprintList>("50-sprints-project.json");
const afterClose = sprintsBody<SprintList>("63-sprints-after-close.json");
const [current, next] = team.sprints;
const book: SprintBook = { team, own: { 2: own } };

describe("the sprint's words", () => {
  it("names a sprint option by its state", () => {
    expect(sprintOption(current)).toBe("Sprint 1 (current)");
    expect(sprintOption(next)).toBe("Sprint 2 (next)");
    expect(sprintOption(afterClose.sprints[0])).toBe("Sprint 1 (closed)");
  });

  it("writes the range as the designer does, and adds a year only outside today's", () => {
    expect(sprintRange(current, "2026-11-04")).toBe("Mon 2 Nov to Fri 6 Nov");
    expect(sprintRange(current, "2027-01-04")).toBe("Mon 2 Nov 2026 to Fri 6 Nov 2026");
  });

  it("says how long a sprint has left, when it starts, or that it is closed", () => {
    expect(sprintTiming(current, "2026-11-02")).toBe("4 days left");
    expect(sprintTiming(current, "2026-11-05")).toBe("1 day left");
    expect(sprintTiming(current, "2026-11-06")).toBe("Last day");
    expect(sprintTiming(next, "2026-11-06")).toBe("Starts Mon 9 Nov");
    expect(sprintTiming(afterClose.sprints[0], "2026-11-06")).toBe("Closed");
  });

  it("writes the note under the progress bar: the Monday to Friday line, a custom length, or the length", () => {
    expect(sprintNote(team)).toBe("Opens at Monday planning, closes at Friday review");
    expect(sprintNote({ ...team, sprint_align_weekdays: false })).toBe("1 week, 5 working days sprints");
    expect(sprintNote({ ...team, sprint_length: "week14" })).toBe("2 weeks sprints");
    expect(sprintNote({ ...team, sprint_length: "custom", sprint_custom_days: 10 })).toBe("10-day sprints");
    // Aligned only means something for 1 week, 5 working days.
    expect(sprintNote({ ...team, sprint_length: "month1" })).toBe("1 month, 30 days sprints");
  });

  it("names the team's length, a custom one in days", () => {
    expect(lengthLabel("week7", 12)).toBe("1 week, 7 days");
    expect(lengthLabel("month6", 12)).toBe("6 months");
    expect(lengthLabel("custom", 12)).toBe("12 days");
  });
});

describe("which sprints a task belongs to", () => {
  const task = (patch: Partial<Task>) => ({ ...sprintsBody<TaskList>("17-tasks-filed.json").tasks[0], ...patch });

  it("uses a project's own pair when it has one, else the team's", () => {
    expect(listFor(book, 2)).toBe(own);
    expect(listFor(book, 1)).toBe(team);
    expect(listFor(book, null)).toBe(team);
  });

  it("lets 'current' stand for every series under All projects, and only itself otherwise", () => {
    expect(boardSprintIds(book, team, team.current_id, true)).toEqual([1, 5]);
    expect(boardSprintIds(book, team, team.next_id, true)).toEqual([2, 6]);
    expect(boardSprintIds(book, team, team.current_id, false)).toEqual([1]);
    // A closed sprint is only itself.
    expect(boardSprintIds({ team: afterClose, own: {} }, afterClose, 1, true)).toEqual([1]);
  });

  it("offers Backlog, the current and the next sprint of the task's own series", () => {
    expect(sprintChoices(book, task({ project_id: 1, sprint_id: 1 }))).toEqual([
      { value: "", label: "Backlog" },
      { value: "1", label: "Sprint 1 (current)" },
      { value: "2", label: "Sprint 2 (next)" },
    ]);
    expect(sprintChoices(book, task({ project_id: 2, sprint_id: null })).map((o) => o.label)).toEqual([
      "Backlog",
      "Sprint 1 (current)",
      "Sprint 2 (next)",
    ]);
    expect(sprintChoices(book, task({ project_id: 2, sprint_id: null })).map((o) => o.value)).toEqual(["", "5", "6"]);
  });

  it("shows the closed sprint a finished task was left in, so the select tells the truth", () => {
    const closed: SprintBook = { team: afterClose, own: {} };
    expect(sprintChoices(closed, task({ project_id: null, sprint_id: 1 })).map((o) => o.label)).toEqual([
      "Backlog",
      "Sprint 1 (closed)",
      "Sprint 2 (current)",
      "Sprint 3 (next)",
    ]);
  });
});

describe("loadSprintBook", () => {
  const projects = sprintsBody<ProjectList>("16-projects-list.json").projects;

  it("reads the team's pair and one pair for each project with its own length", async () => {
    const list = vi.fn(async (project?: number) => (project === 2 ? own : team));
    const result = await loadSprintBook({ sprints: { list } }, projects.map((p) => (p.id === 2 ? { ...p, sprint_length: "week14" as const } : p)));
    expect(result).toEqual({ team, own: { 2: own } });
    expect(list.mock.calls).toEqual([[undefined, undefined], [2, undefined]]);
  });

  it("asks for the named team's sprints when one is given (the meeting page's drawer)", async () => {
    const list = vi.fn(async (project?: number) => (project === 2 ? own : team));
    await loadSprintBook({ sprints: { list } }, projects.map((p) => (p.id === 2 ? { ...p, sprint_length: "week14" as const } : p)), 4);
    expect(list.mock.calls).toEqual([[undefined, 4], [2, 4]]);
  });

  it("asks only for the team's when no project has its own length", async () => {
    const list = vi.fn(async () => team);
    expect(await loadSprintBook({ sprints: { list } }, projects)).toEqual({ team, own: {} });
    expect(list).toHaveBeenCalledTimes(1);
    expect(await loadSprintBook({ sprints: { list } }, null)).toEqual({ team, own: {} });
  });

  it("is null when any read fails", async () => {
    const list = vi.fn(async (project?: number) => {
      if (project === 2) throw new Error("down");
      return team;
    });
    expect(await loadSprintBook({ sprints: { list } }, projects.map((p) => ({ ...p, sprint_length: "week14" as const })))).toBeNull();
  });
});
