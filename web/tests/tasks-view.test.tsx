// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TasksView } from "@/components/app/tasks-view";
import { ApiError } from "@/lib/api/client";
import type { Task, TaskList } from "@/lib/api/types";
import { tasksBody } from "./helpers";

// The board is fed the bodies captured from the running API (docs/fe-captures/tasks-w2/, cited in
// docs/fe-integration-guide-tasks.md). The capture is Dan's: four tasks, all assigned. Where a test
// needs a task nobody owns, it takes a captured task and clears its assignee, as Dan reassigning it
// would (capture 51).

const mocks = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { tasks: mocks } }));

const board = tasksBody<TaskList>("17-tasks.json");
const noOwner = (task: Task): Task => ({
  ...task,
  assignee: null,
  can_claim: true,
});
const withNoOwner: TaskList = { ...board, tasks: board.tasks.map((t, i) => (i === 0 ? noOwner(t) : t)) };

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-03T12:00:00Z"));
  Object.values(mocks).forEach((fn) => fn.mockReset());
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the Tasks screen", () => {
  it("draws the title, New task, the three filter chips, Group by and the three columns", () => {
    render(<TasksView initial={board} />);
    expect(screen.getByRole("heading", { level: 1, name: "Tasks" })).toBeTruthy();
    expect(screen.getByText("Action items from your meetings, assigned to the people responsible.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "New task" })).toBeTruthy();
    for (const chip of ["All tasks", "My tasks", "Needs owner"]) expect(screen.getByRole("button", { name: chip })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Date assigned" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Due date" })).toBeTruthy();
    for (const column of ["To do", "In progress", "Done"]) expect(screen.getByRole("region", { name: column })).toBeTruthy();
  });

  it("leaves out what needs projects, sprints and teammates: no tabs, sprint or milestone controls", () => {
    render(<TasksView initial={board} />);
    for (const absent of ["All projects", "Board", "Backlog", "Close sprint", "Show launch tasks"]) {
      expect(screen.queryByText(absent)).toBeNull();
    }
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("puts each captured task on a card with its title, meeting, due day, owner and a New pill", () => {
    render(<TasksView initial={board} />);
    const todo = screen.getByRole("region", { name: "To do" });
    expect(within(todo).getByText("Set up the tracking plan")).toBeTruthy();
    expect(within(todo).getAllByText("Google Meet nat-aaaa-bbb, 3 Nov").length).toBe(4);
    expect(within(todo).getByText("Fri 6 Nov")).toBeTruthy();
    expect(within(todo).getByText("Tomorrow")).toBeTruthy(); // 4 Nov
    expect(within(todo).getAllByText("New").length).toBe(4);
    expect(within(todo).getAllByTitle("Chidi Eze")[0].textContent).toBe("CE");
    expect(within(screen.getByRole("region", { name: "Done" })).getByText("Drag tasks here")).toBeTruthy();
  });

  it("groups by the day assigned, then by due date when asked", async () => {
    render(<TasksView initial={board} />);
    expect(screen.getByText("Assigned Today, Tue 3 Nov")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Due date" }));
    expect(screen.getByText("Due Fri 6 Nov")).toBeTruthy();
    expect(screen.getByText("Due Tomorrow")).toBeTruthy();
    expect(screen.getAllByText("No due date").length).toBeGreaterThan(0); // the group and the cards
    expect(screen.getByRole("button", { name: "Due date" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("filters to My tasks and Needs owner on the client", async () => {
    render(<TasksView initial={withNoOwner} />);
    const ownerless = board.tasks[0].title;
    await userEvent.click(screen.getByRole("button", { name: "Needs owner" }));
    expect(screen.getByText(ownerless)).toBeTruthy();
    expect(screen.queryByText("Share a draft of the dashboards")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "My tasks" }));
    expect(screen.queryByText(ownerless)).toBeNull(); // none of the captured tasks is Dan's own
    await userEvent.click(screen.getByRole("button", { name: "All tasks" }));
    expect(screen.getByText(ownerless)).toBeTruthy();
  });

  it("shows the attention banner only when a task needs an owner, and Review opens it", async () => {
    const { rerender } = render(<TasksView initial={board} />);
    expect(screen.queryByText(/need attention|needs attention/)).toBeNull();
    rerender(<TasksView initial={withNoOwner} />);
    cleanup();
    render(<TasksView initial={withNoOwner} />);
    expect(screen.getByText("1 task needs attention: 1 needs an owner")).toBeTruthy();
    expect(screen.getByText(/These ones could not be matched with confidence/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Review" }));
    const drawer = screen.getByRole("dialog", { name: "Task" });
    expect((within(drawer).getByLabelText("Task title") as HTMLTextAreaElement).value).toBe(board.tasks[0].title);
  });

  it("says it could not load, and Try again asks the API once more", async () => {
    mocks.list.mockResolvedValue(board);
    render(<TasksView initial={null} />);
    expect(screen.getByText(/Could not load tasks/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByRole("region", { name: "To do" })).toBeTruthy());
    expect(mocks.list).toHaveBeenCalledWith({ limit: 500 });
  });

  it("New task makes a blank task and opens it in the drawer", async () => {
    const made = { ...board.tasks[0], id: 99, title: "", source: "manual", from_meeting: false, meeting: null, quote: null, assignee: null } as Task;
    mocks.create.mockResolvedValue(made);
    render(<TasksView initial={board} />);
    await userEvent.click(screen.getByRole("button", { name: "New task" }));
    expect(mocks.create).toHaveBeenCalledWith({});
    const drawer = await screen.findByRole("dialog", { name: "Task" });
    expect((within(drawer).getByLabelText("Task title") as HTMLTextAreaElement).value).toBe("");
    expect(within(drawer).queryByText("FROM THE MEETING")).toBeNull(); // a manual task has no meeting
  });
});

describe("dragging a card", () => {
  function drag(card: HTMLElement, column: HTMLElement) {
    const dataTransfer = { setData: vi.fn(), effectAllowed: "" };
    fireEvent.dragStart(card, { dataTransfer });
    fireEvent.dragOver(column, { dataTransfer });
    fireEvent.drop(column, { dataTransfer });
  }

  it("moves it to the column it is dropped on, at once, and keeps the API's answer", async () => {
    const first = board.tasks[0];
    mocks.update.mockResolvedValue({ ...first, status: "doing", is_new: false });
    render(<TasksView initial={board} />);
    const card = screen.getByText(first.title).closest(".tk-card") as HTMLElement;
    drag(card, screen.getByRole("region", { name: "In progress" }));
    expect(within(screen.getByRole("region", { name: "In progress" })).getByText(first.title)).toBeTruthy();
    await waitFor(() => expect(mocks.update).toHaveBeenCalledWith(first.id, { status: "doing" }));
  });

  it("puts the card back and says why when the API refuses", async () => {
    const first = board.tasks[0];
    mocks.update.mockRejectedValue(new ApiError(403, "team_forbidden", "You can't do that to this task. Ask an owner or admin of your team."));
    render(<TasksView initial={board} />);
    drag(screen.getByText(first.title).closest(".tk-card") as HTMLElement, screen.getByRole("region", { name: "Done" }));
    await waitFor(() => expect(within(screen.getByRole("region", { name: "To do" })).getByText(first.title)).toBeTruthy());
    expect(await screen.findByText(/You can't do that to this task/)).toBeTruthy();
  });

  it("does not let a card be dragged that the person may not change", () => {
    const locked: TaskList = { ...board, tasks: board.tasks.map((t) => ({ ...t, can_update: false })) };
    render(<TasksView initial={locked} />);
    expect(screen.getByText(board.tasks[0].title).closest(".tk-card")?.getAttribute("draggable")).toBe("false");
  });
});
