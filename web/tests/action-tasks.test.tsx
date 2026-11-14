// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ActionTasks } from "@/components/app/action-tasks";
import { NotesHtml } from "@/components/app/notes-html";
import type { MeetingTasks, TaskList } from "@/lib/api/types";
import { actionSummary, dueLabel, dueOptions, groupTasks } from "@/lib/tasks";
import { tasksBody } from "./helpers";

const mocks = vi.hoisted(() => ({ list: vi.fn(), update: vi.fn(), remove: vi.fn() }));
const others = vi.hoisted(() => ({ projects: vi.fn(), milestones: vi.fn(), sprints: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({
  api: { tasks: mocks, projects: { list: others.projects }, milestones: { list: others.milestones }, sprints: { list: others.sprints } },
}));

const meeting = tasksBody<MeetingTasks>("18-meeting-tasks.json");
const board = tasksBody<TaskList>("17-tasks.json");

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-03T12:00:00Z"));
  Object.values(mocks).forEach((fn) => fn.mockReset());
  Object.values(others).forEach((fn) => fn.mockReset());
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the drawer reads the MEETING's team (multi-team)", () => {
  const ask = async (team?: number | null) => {
    mocks.list.mockResolvedValue(board);
    others.projects.mockResolvedValue({ projects: [] });
    others.milestones.mockResolvedValue({ milestones: [] });
    others.sprints.mockRejectedValue(new Error("not needed"));
    render(<ActionTasks initial={meeting.tasks} team={team} />);
    await userEvent.click(screen.getByText("Set up the tracking plan"));
    await waitFor(() => expect(others.sprints).toHaveBeenCalled());
  };

  it("asks for the people, projects, milestones and sprints of the meeting's team, not the tab's", async () => {
    await ask(4);
    expect(mocks.list).toHaveBeenCalledWith({ limit: 1 }, 4);
    expect(others.projects).toHaveBeenCalledWith(4);
    expect(others.milestones).toHaveBeenCalledWith(4);
    expect(others.sprints).toHaveBeenCalledWith(undefined, 4);
  });

  it("a private meeting names no team (null: the default team), and with the switch off nothing is named", async () => {
    await ask(null);
    expect(mocks.list).toHaveBeenCalledWith({ limit: 1 }, null);
    cleanup();
    Object.values(mocks).forEach((fn) => fn.mockReset());
    Object.values(others).forEach((fn) => fn.mockReset());
    await ask(undefined);
    expect(mocks.list).toHaveBeenCalledWith({ limit: 1 }, undefined);
    expect(others.projects).toHaveBeenCalledWith(undefined);
  });
});

describe("the meeting page's Action items as tasks", () => {
  it("shows the summary with View board, and a row per task with due day and owner", () => {
    render(<ActionTasks initial={meeting.tasks} />);
    expect(screen.getByRole("heading", { name: "Action items" })).toBeTruthy();
    const link = screen.getByRole("link", { name: "4 tasks created, assigned to 2 people · View board" });
    expect(link.getAttribute("href")).toBe("/tasks");
    expect(screen.getByText("Set up the tracking plan")).toBeTruthy();
    expect(screen.getAllByText("Chidi Eze").length).toBe(2);
    expect(screen.getByText("Fri 6 Nov")).toBeTruthy();
  });

  it("draws Needs owner for a task nobody has", () => {
    render(<ActionTasks initial={[{ ...meeting.tasks[0], assignee: null }]} />);
    expect(screen.getByText("Needs owner")).toBeTruthy();
  });

  it("ticks a task done without opening it, and puts it back if the API refuses", async () => {
    mocks.update.mockResolvedValueOnce({ ...meeting.tasks[0], status: "done" });
    render(<ActionTasks initial={meeting.tasks} />);
    const box = screen.getAllByRole("checkbox")[0];
    await userEvent.click(box);
    expect(mocks.update).toHaveBeenCalledWith(meeting.tasks[0].id, { status: "done" });
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(box.getAttribute("aria-checked")).toBe("true"));
    mocks.update.mockRejectedValueOnce(new Error("no"));
    await userEvent.click(box);
    await waitFor(() => expect(box.getAttribute("aria-checked")).toBe("true")); // back to done
    expect(await screen.findByText("Could not update this task. Try again.")).toBeTruthy();
  });

  it("opens the drawer on a row, with the people list read once", async () => {
    mocks.list.mockResolvedValue(board);
    render(<ActionTasks initial={meeting.tasks} />);
    await userEvent.click(screen.getByText("Set up the tracking plan"));
    const drawer = await screen.findByRole("dialog", { name: "Task" });
    await waitFor(() => expect(within(drawer).getByRole("option", { name: "Amina Bello" })).toBeTruthy());
    expect(mocks.list).toHaveBeenCalledTimes(1);
    await userEvent.click(within(drawer).getByRole("button", { name: "Done" }));
    await userEvent.click(screen.getByText("Check the vendor site for the spelling"));
    expect(mocks.list).toHaveBeenCalledTimes(1);
  });

  it("replaces only the Action items part of the notes", () => {
    const html = "<h2>Summary</h2><p>Short.</p><h2>Action items</h2><ul><li>Old bullet</li></ul><h2>Decisions</h2><p>One.</p>";
    const { container } = render(<NotesHtml html={html} actions={<p>THE TASKS</p>} />);
    expect(screen.getByText("THE TASKS")).toBeTruthy();
    expect(screen.queryByText("Old bullet")).toBeNull();
    expect(screen.getByText("Short.")).toBeTruthy();
    expect(screen.getByText("One.")).toBeTruthy();
    expect(container.querySelectorAll("section.notes-sec").length).toBe(3);
    expect(container.querySelector(".notes-sec-actions")?.textContent).toBe("THE TASKS");
  });

  it("leaves the notes alone when there are no tasks to show", () => {
    render(<NotesHtml html="<h2>Action items</h2><ul><li>Old bullet</li></ul>" />);
    expect(screen.getByText("Old bullet")).toBeTruthy();
  });
});

describe("the date words", () => {
  it("writes due days as the designer does", () => {
    expect(dueLabel(null, "2026-11-03")).toBe("No due date");
    expect(dueLabel("2026-11-03", "2026-11-03")).toBe("Today");
    expect(dueLabel("2026-11-04", "2026-11-03")).toBe("Tomorrow");
    expect(dueLabel("2026-11-06", "2026-11-03")).toBe("Fri 6 Nov");
    expect(dueLabel("2026-12-31", "2026-12-30")).toBe("Tomorrow");
    expect(dueLabel("2027-01-01", "2026-12-30")).toBe("Fri 1 Jan");
  });

  it("offers a week of days and keeps a date outside it", () => {
    const options = dueOptions("2026-11-03", "2026-12-25");
    expect(options[0]).toEqual({ value: "", label: "No due date" });
    expect(options.map((o) => o.label).slice(1, 4)).toEqual(["Today", "Tomorrow", "Thu 5 Nov"]);
    expect(options.at(-1)).toEqual({ value: "2026-12-25", label: "Fri 25 Dec" });
  });

  it("groups by due date earliest first with no date last, and by day assigned newest first", () => {
    const tasks = board.tasks;
    const byDue = groupTasks(tasks, "due", "2026-11-03").map((g) => g.label);
    expect(byDue).toEqual(["Due Tomorrow", "Due Fri 6 Nov", "Due Wed 11 Nov", "No due date"]);
    expect(groupTasks(tasks, "assigned", "2026-11-03").map((g) => g.label)).toEqual(["Assigned Today, Tue 3 Nov"]);
  });

  it("counts people once and says person for one", () => {
    expect(actionSummary(meeting.tasks)).toBe("4 tasks created, assigned to 2 people");
    expect(actionSummary([meeting.tasks[0]])).toBe("1 task created, assigned to 1 person");
  });
});
