// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TaskCard } from "@/components/app/task-card";
import type { Task, TaskList } from "@/lib/api/types";
import { groupLabel, groupTasks, isOverdue, taskDueText } from "@/lib/tasks";
import { tasksBody } from "./helpers";

// B11 of docs/ui/audit-2026-11-07-main-v2.md: the designer's overdue words (D:1511-1512, D:1779,
// D:2039). A day before today is "Overdue, Fri 30 Oct" on the card (red), and the Group-by-Due-date
// heading says "Overdue, was due Fri 30 Oct". A finished task keeps its plain grey day.

afterEach(cleanup);

const base = tasksBody<TaskList>("17-tasks.json").tasks[0];
const TODAY = "2026-11-02"; // Mon 2 Nov, the day the designer's sample is set on
const task = (patch: Partial<Task>): Task => ({ ...base, ...patch });
const card = (t: Task) =>
  render(<TaskCard task={t} today={TODAY} onOpen={vi.fn()} onDragStart={vi.fn()} onDragEnd={vi.fn()} />);

describe("the overdue wording", () => {
  it("is overdue only when the day is before today", () => {
    expect(isOverdue("2026-10-30", TODAY)).toBe(true);
    expect(isOverdue("2026-11-02", TODAY)).toBe(false);
    expect(isOverdue("2026-11-03", TODAY)).toBe(false);
    expect(isOverdue(null, TODAY)).toBe(false);
    expect(isOverdue("", TODAY)).toBe(false);
  });

  it("writes the due line as the designer does", () => {
    expect(taskDueText({ due_date: "2026-10-30", status: "todo" }, TODAY)).toBe("Overdue, Fri 30 Oct");
    expect(taskDueText({ due_date: "2026-10-30", status: "doing" }, TODAY)).toBe("Overdue, Fri 30 Oct");
    expect(taskDueText({ due_date: "2026-10-30", status: "done" }, TODAY)).toBe("Fri 30 Oct");
    expect(taskDueText({ due_date: "2026-11-02", status: "todo" }, TODAY)).toBe("Today");
    expect(taskDueText({ due_date: "2026-11-03", status: "todo" }, TODAY)).toBe("Tomorrow");
    expect(taskDueText({ due_date: null, status: "todo" }, TODAY)).toBe("No due date");
  });

  it("heads an overdue group 'Overdue, was due Fri 30 Oct' and leaves the other headings alone", () => {
    expect(groupLabel("due", "2026-10-30", TODAY)).toBe("Overdue, was due Fri 30 Oct");
    expect(groupLabel("due", "2026-11-02", TODAY)).toBe("Due today");
    expect(groupLabel("due", "2026-11-03", TODAY)).toBe("Due Tomorrow");
    expect(groupLabel("due", "2026-11-06", TODAY)).toBe("Due Fri 6 Nov");
    expect(groupLabel("due", "", TODAY)).toBe("No due date");
    expect(groupLabel("assigned", "2026-10-30", TODAY)).toBe("Assigned Fri 30 Oct");
  });

  it("puts the overdue group first when grouping by due date", () => {
    const list = [task({ id: 1, due_date: "2026-11-03" }), task({ id: 2, due_date: "2026-10-30" }), task({ id: 3, due_date: null })];
    expect(groupTasks(list, "due", TODAY).map((g) => g.label)).toEqual(["Overdue, was due Fri 30 Oct", "Due Tomorrow", "No due date"]);
  });

  it("draws the card's due label in red when the task is open and late", () => {
    card(task({ due_date: "2026-10-30", status: "todo" }));
    const label = screen.getByText("Overdue, Fri 30 Oct");
    expect(label.className).toContain("tk-due-overdue");
  });

  it("keeps a finished late task's plain grey day", () => {
    card(task({ due_date: "2026-10-30", status: "done" }));
    const label = screen.getByText("Fri 30 Oct");
    expect(label.className).toContain("tk-due-done");
    expect(label.className).not.toContain("tk-due-overdue");
  });

  it("keeps Today amber and a later day plain", () => {
    card(task({ due_date: "2026-11-02", status: "todo" }));
    expect(screen.getByText("Today").className).toContain("tk-due-today");
    cleanup();
    card(task({ due_date: "2026-11-05", status: "todo" }));
    expect(screen.getByText("Thu 5 Nov").className).toBe("tk-due");
  });
});
