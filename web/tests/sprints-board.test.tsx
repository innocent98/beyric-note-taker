// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ActionTasks } from "@/components/app/action-tasks";
import { TaskDrawer } from "@/components/app/task-drawer";
import { TasksView } from "@/components/app/tasks-view";
import { ApiError } from "@/lib/api/client";
import type { ProjectList, SprintClosed, SprintList, Task, TaskList } from "@/lib/api/types";
import type { SprintBook } from "@/lib/sprints";
import { sprintsBody } from "./helpers";

// The sprint screens on Tasks, fed the bodies captured from the running API
// (docs/fe-captures/sprints-w9/). Capture 17 is Dan's board after the real extraction run: task 1
// "Finish the portal checkout" is due 6 Nov so it is in Sprint 1 (current); tasks 2 "Book the venue
// for the offsite" and 3 "Review the copy on the portal" have no due date, so they are in the Backlog.

const mocks = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() }));
const sprintApi = vi.hoisted(() => ({ list: vi.fn(), close: vi.fn() }));
const listing = vi.hoisted(() => ({ projects: vi.fn(), milestones: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    tasks: mocks,
    sprints: sprintApi,
    projects: { list: listing.projects },
    milestones: { list: listing.milestones },
  },
}));

const board = sprintsBody<TaskList>("17-tasks-filed.json");
const projects = sprintsBody<ProjectList>("16-projects-list.json").projects;
const team = sprintsBody<SprintList>("09-sprints-first.json"); // Sprint 1 current (2 to 6 Nov), Sprint 2 next
const ownPair = sprintsBody<SprintList>("50-sprints-project.json"); // Offsite's own: Sprint 1 (id 5), Sprint 2 (id 6)
const afterClose = sprintsBody<SprintList>("63-sprints-after-close.json");
const closed = sprintsBody<SprintClosed>("61-close-current.json");
const book: SprintBook = { team, own: {} };
const byTitle = (title: string): Task => board.tasks.find((t) => t.title === title)!;
const view = (props: Partial<React.ComponentProps<typeof TasksView>> = {}) =>
  render(<TasksView initial={board} projects={projects} sprints={book} {...props} />);
const columns = () => Array.from(document.querySelectorAll(".tk-col")).map((c) => c.getAttribute("aria-label"));

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-04T12:00:00Z"));
  for (const group of [mocks, sprintApi, listing]) Object.values(group).forEach((fn) => fn.mockReset());
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the sprint card and selector", () => {
  it("draws the current sprint's name, dates, time left, progress and note, as the designer's card does", () => {
    view();
    const card = document.querySelector(".tk-sprint") as HTMLElement;
    expect(within(card).getByText("Sprint 1")).toBeTruthy();
    expect(within(card).getByText("Mon 2 Nov to Fri 6 Nov")).toBeTruthy();
    expect(within(card).getByText("2 days left")).toBeTruthy();
    expect(within(card).getByText("0 of 1 done · Opens at Monday planning, closes at Friday review")).toBeTruthy();
    expect(within(card).getByRole("button", { name: "Close sprint" })).toBeTruthy();
  });

  it("lists the sprints in the selector with their state, current first on the board", () => {
    view();
    const select = screen.getByRole("combobox", { name: "Sprint" }) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.text)).toEqual(["Sprint 1 (current)", "Sprint 2 (next)"]);
    expect(select.value).toBe("1");
  });

  it("puts only the tasks of the sprint shown on the board, and none under the next sprint", async () => {
    view();
    expect(screen.getByText("Finish the portal checkout")).toBeTruthy();
    expect(screen.queryByText("Book the venue for the offsite")).toBeNull();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Sprint" }), "Sprint 2 (next)");
    expect(screen.queryByText("Finish the portal checkout")).toBeNull();
    expect(screen.getAllByText("Drag tasks here").length).toBe(3);
    const card = document.querySelector(".tk-sprint") as HTMLElement;
    expect(within(card).getByText("Starts Mon 9 Nov")).toBeTruthy();
    expect(within(card).queryByRole("button", { name: "Close sprint" })).toBeNull();
  });

  it("counts done tasks of the sprint, whatever the person chips say", async () => {
    const done: TaskList = { ...board, tasks: board.tasks.map((t) => (t.id === 1 ? { ...t, status: "done" as const } : t)) };
    view({ initial: done });
    expect(within(document.querySelector(".tk-sprint") as HTMLElement).getByText(/^1 of 1 done/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Needs owner" }));
    expect(within(document.querySelector(".tk-sprint") as HTMLElement).getByText(/^1 of 1 done/)).toBeTruthy();
  });

  it("hides Close sprint from someone who may not close it", () => {
    view({ sprints: { team: { ...team, can_manage: false }, own: {} } });
    expect(screen.queryByRole("button", { name: "Close sprint" })).toBeNull();
    expect(screen.getByText("Sprint 1")).toBeTruthy();
  });

  it("is not drawn at all when the page was given no sprints, and says so with Try again when they failed", async () => {
    const { unmount } = render(<TasksView initial={board} projects={projects} />);
    expect(screen.queryByRole("combobox", { name: "Sprint" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Backlog" })).toBeNull();
    expect(screen.queryByText("Could not load sprints.")).toBeNull();
    expect(screen.getAllByText(/Book the venue|Finish the portal|Review the copy/).length).toBe(3);
    unmount();
    render(<TasksView initial={board} projects={projects} sprints={null} />);
    expect(screen.getByText("Could not load sprints.")).toBeTruthy();
    sprintApi.list.mockResolvedValue(team);
    mocks.list.mockResolvedValue(board);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Sprint" })).toBeTruthy());
    expect(screen.queryByText("Could not load sprints.")).toBeNull();
  });
});

describe("Close sprint", () => {
  it("closes the sprint, says how many tasks moved in the designer's words, and shows the new current sprint", async () => {
    sprintApi.close.mockResolvedValue(closed);
    sprintApi.list.mockResolvedValue(afterClose);
    mocks.list.mockResolvedValue(board);
    view();
    await userEvent.click(screen.getByRole("button", { name: "Close sprint" }));
    expect(sprintApi.close).toHaveBeenCalledWith(1);
    expect(await screen.findByText("Sprint 1 closed. 3 unfinished tasks moved to Sprint 2")).toBeTruthy();
    await waitFor(() => expect((screen.getByRole("combobox", { name: "Sprint" }) as HTMLSelectElement).value).toBe("2"));
    expect(Array.from((screen.getByRole("combobox", { name: "Sprint" }) as HTMLSelectElement).options).map((o) => o.text)).toEqual([
      "Sprint 1 (closed)",
      "Sprint 2 (current)",
      "Sprint 3 (next)",
    ]);
  });

  it("says '1 unfinished task' for one", async () => {
    sprintApi.close.mockResolvedValue({ ...closed, moved: 1 });
    sprintApi.list.mockResolvedValue(afterClose);
    mocks.list.mockResolvedValue(board);
    view();
    await userEvent.click(screen.getByRole("button", { name: "Close sprint" }));
    expect(await screen.findByText("Sprint 1 closed. 1 unfinished task moved to Sprint 2")).toBeTruthy();
  });

  it("reads the sprints again, without an alarm, when the sprint had already closed (409 sprint_not_current)", async () => {
    sprintApi.close.mockRejectedValue(new ApiError(409, "sprint_not_current", "Only the current sprint can be closed."));
    sprintApi.list.mockResolvedValue(afterClose);
    mocks.list.mockResolvedValue(board);
    view();
    await userEvent.click(screen.getByRole("button", { name: "Close sprint" }));
    await waitFor(() => expect(sprintApi.list).toHaveBeenCalled());
    expect(screen.queryByText("Only the current sprint can be closed.")).toBeNull();
  });

  it("shows the API's message for any other refusal", async () => {
    sprintApi.close.mockRejectedValue(new ApiError(403, "team_forbidden", "Only an owner or admin of the team can do that."));
    view();
    await userEvent.click(screen.getByRole("button", { name: "Close sprint" }));
    expect(await screen.findByText("Only an owner or admin of the team can do that.")).toBeTruthy();
  });
});

describe("Board and Backlog", () => {
  const backlog = () => screen.getByRole("group", { name: "View" });
  const section = (name: string) => screen.getByRole("region", { name }) as HTMLElement;

  it("has the two tabs, Board first, and hides the sprint selector on the Backlog", async () => {
    view();
    expect(within(backlog()).getAllByRole("button").map((b) => b.textContent)).toEqual(["Board", "Backlog"]);
    expect(columns()).toEqual(["To do", "In progress", "Done"]);
    await userEvent.click(within(backlog()).getByRole("button", { name: "Backlog" }));
    expect(screen.queryByRole("combobox", { name: "Sprint" })).toBeNull();
    expect(columns()).toEqual([]);
    // The sprint card stays above, as in the design.
    expect(screen.getByText("Mon 2 Nov to Fri 6 Nov")).toBeTruthy();
  });

  it("draws the next sprint and the Backlog as two cards with the designer's sentences and counts", async () => {
    view();
    await userEvent.click(within(backlog()).getByRole("button", { name: "Backlog" }));
    const next = section("Sprint 2");
    expect(within(next).getByText("Next sprint, Mon 9 Nov to Fri 13 Nov. Tasks due after this sprint land here.")).toBeTruthy();
    expect(within(next).getByText("Nothing planned yet.")).toBeTruthy();
    const rest = section("Backlog");
    expect(within(rest).getByText("Not scheduled yet. Tasks with no due date land here.")).toBeTruthy();
    expect(within(rest).getByText("2")).toBeTruthy();
    expect(within(rest).getByText("Book the venue for the offsite")).toBeTruthy();
    expect(within(rest).getByText("Review the copy on the portal")).toBeTruthy();
    expect(within(rest).queryByText("Finish the portal checkout")).toBeNull();
  });

  it("says 'The backlog is empty.' and leaves finished tasks out", async () => {
    const none: TaskList = { ...board, tasks: board.tasks.map((t) => ({ ...t, sprint_id: null, status: "done" as const })) };
    view({ initial: none });
    await userEvent.click(within(backlog()).getByRole("button", { name: "Backlog" }));
    expect(within(section("Backlog")).getByText("The backlog is empty.")).toBeTruthy();
  });

  it("plans a task from its row: one PATCH of sprint_id, the row moves to the next sprint, the drawer stays shut", async () => {
    mocks.update.mockResolvedValue({ ...byTitle("Book the venue for the offsite"), sprint_id: 2 });
    view();
    await userEvent.click(within(backlog()).getByRole("button", { name: "Backlog" }));
    const select = screen.getByRole("combobox", { name: "Sprint of Book the venue for the offsite" }) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.text)).toEqual(["Backlog", "Sprint 1 (current)", "Sprint 2 (next)"]);
    expect(select.value).toBe("");
    await userEvent.selectOptions(select, "Sprint 2 (next)");
    expect(mocks.update).toHaveBeenCalledWith(2, { sprint_id: 2 });
    await waitFor(() => expect(within(section("Sprint 2")).getByText("Book the venue for the offsite")).toBeTruthy());
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("sends null for Backlog", async () => {
    mocks.update.mockResolvedValue({ ...byTitle("Finish the portal checkout"), sprint_id: null });
    const planned: TaskList = { ...board, tasks: board.tasks.map((t) => (t.id === 2 ? { ...t, sprint_id: 2 } : t)) };
    view({ initial: planned });
    await userEvent.click(within(backlog()).getByRole("button", { name: "Backlog" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Sprint of Book the venue for the offsite" }), "Backlog");
    expect(mocks.update).toHaveBeenCalledWith(2, { sprint_id: null });
  });

  it("shows a refusal and leaves the row where it was", async () => {
    mocks.update.mockRejectedValue(new ApiError(403, "team_forbidden", "You can't do that to this task. Ask an owner or admin of your team."));
    view();
    await userEvent.click(within(backlog()).getByRole("button", { name: "Backlog" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Sprint of Book the venue for the offsite" }), "Sprint 1 (current)");
    expect(await screen.findByText("You can't do that to this task. Ask an owner or admin of your team.")).toBeTruthy();
    expect((screen.getByRole("combobox", { name: "Sprint of Book the venue for the offsite" }) as HTMLSelectElement).value).toBe("");
  });

  it("switches the row's select off for a task its person may not plan (can_edit false)", async () => {
    const locked: TaskList = { ...board, tasks: board.tasks.map((t) => (t.id === 2 ? { ...t, can_edit: false } : t)) };
    view({ initial: locked });
    await userEvent.click(within(backlog()).getByRole("button", { name: "Backlog" }));
    expect((screen.getByRole("combobox", { name: "Sprint of Book the venue for the offsite" }) as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByRole("combobox", { name: "Sprint of Review the copy on the portal" }) as HTMLSelectElement).disabled).toBe(false);
  });

  it("opens the drawer from a row, with the Sprint row showing where the task is", async () => {
    view();
    await userEvent.click(within(backlog()).getByRole("button", { name: "Backlog" }));
    await userEvent.click(screen.getByText("Review the copy on the portal"));
    const dialog = within(screen.getByRole("dialog"));
    expect((dialog.getByLabelText("Sprint") as HTMLSelectElement).value).toBe("");
    expect(Array.from((dialog.getByLabelText("Sprint") as HTMLSelectElement).options).map((o) => o.text)).toEqual([
      "Backlog",
      "Sprint 1 (current)",
      "Sprint 2 (next)",
    ]);
  });
});

describe("Carried over", () => {
  const carried: TaskList = { ...board, tasks: board.tasks.map((t) => (t.id === 1 ? { ...t, carried: true } : t)) };

  it("is a badge on an unfinished carried task, in both views, and gone once it is done", async () => {
    view({ initial: carried });
    const card = screen.getByText("Finish the portal checkout").closest(".tk-card") as HTMLElement;
    expect(within(card).getByText("Carried over")).toBeTruthy();
    cleanup();
    const done: TaskList = { ...board, tasks: board.tasks.map((t) => (t.id === 1 ? { ...t, carried: true, status: "done" as const } : t)) };
    view({ initial: done });
    expect(screen.queryByText("Carried over")).toBeNull();
    cleanup();
    view({ initial: { ...carried, tasks: carried.tasks.map((t) => (t.id === 1 ? { ...t, sprint_id: null } : t)) } });
    await userEvent.click(within(screen.getByRole("group", { name: "View" })).getByRole("button", { name: "Backlog" }));
    expect(within(screen.getByRole("region", { name: "Backlog" })).getByText("Carried over")).toBeTruthy();
  });
});

describe("New task", () => {
  it("goes into the sprint the Board shows", async () => {
    mocks.create.mockResolvedValue({ ...byTitle("Finish the portal checkout"), id: 9, title: "", sprint_id: 1 });
    view();
    await userEvent.click(screen.getByRole("button", { name: "New task" }));
    expect(mocks.create).toHaveBeenCalledWith({ sprint_id: 1 });
  });

  it("goes into the next sprint when the Board shows it, and into the Backlog (null, on purpose) from the Backlog view", async () => {
    mocks.create
      .mockResolvedValueOnce({ ...byTitle("Finish the portal checkout"), id: 9, title: "", sprint_id: 2 })
      .mockResolvedValueOnce({ ...byTitle("Finish the portal checkout"), id: 10, title: "", sprint_id: null });
    view();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Sprint" }), "Sprint 2 (next)");
    await userEvent.click(screen.getByRole("button", { name: "New task" }));
    expect(mocks.create).toHaveBeenLastCalledWith({ sprint_id: 2 });
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    await userEvent.click(within(screen.getByRole("group", { name: "View" })).getByRole("button", { name: "Backlog" }));
    await userEvent.click(screen.getByRole("button", { name: "New task" }));
    expect(mocks.create).toHaveBeenLastCalledWith({ sprint_id: null });
  });

  it("goes into the Backlog, not a closed sprint, when the Board shows a closed one", async () => {
    mocks.create.mockResolvedValue({ ...byTitle("Finish the portal checkout"), id: 9, title: "", sprint_id: null });
    view({ sprints: { team: afterClose, own: {} } });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Sprint" }), "Sprint 1 (closed)");
    expect(within(document.querySelector(".tk-sprint") as HTMLElement).getByText("Closed")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "New task" }));
    expect(mocks.create).toHaveBeenCalledWith({ sprint_id: null });
  });

  it("sends the project tab's project with the sprint", async () => {
    mocks.create.mockResolvedValue({ ...byTitle("Finish the portal checkout"), id: 9, title: "" });
    view();
    await userEvent.click(within(screen.getByRole("group", { name: "Project" })).getByRole("button", { name: "Client Portal" }));
    await userEvent.click(screen.getByRole("button", { name: "New task" }));
    expect(mocks.create).toHaveBeenCalledWith({ project_id: 1, sprint_id: 1 });
  });
});

describe("a project with its own sprint length", () => {
  const owned = projects.map((p) => (p.id === 2 ? { ...p, sprint_length: "week14" as const } : p));
  const withOwn: SprintBook = { team, own: { 2: ownPair } };
  const planned: TaskList = {
    ...board,
    tasks: board.tasks.map((t) => (t.id === 2 ? { ...t, sprint_id: 5 } : t)),
  };

  it("shows its own sprints on its tab, and its tasks in the current sprint under All projects", async () => {
    view({ initial: planned, projects: owned, sprints: withOwn });
    // All projects: Sprint 1 of the team and Sprint 1 of Offsite are both "current".
    expect(screen.getByText("Finish the portal checkout")).toBeTruthy();
    expect(screen.getByText("Book the venue for the offsite")).toBeTruthy();
    expect(within(document.querySelector(".tk-sprint") as HTMLElement).getByText(/^0 of 2 done/)).toBeTruthy();
    await userEvent.click(within(screen.getByRole("group", { name: "Project" })).getByRole("button", { name: "Offsite" }));
    expect(within(document.querySelector(".tk-sprint") as HTMLElement).getByText("Fri 6 Nov to Thu 19 Nov")).toBeTruthy();
    expect(screen.getByText("Book the venue for the offsite")).toBeTruthy();
    expect(screen.queryByText("Finish the portal checkout")).toBeNull();
    expect(within(document.querySelector(".tk-sprint") as HTMLElement).getByText("2 weeks sprints", { exact: false })).toBeTruthy();
  });

  it("offers the project's own sprints in its rows' select and the drawer, and sends its sprint id", async () => {
    mocks.update.mockResolvedValue({ ...board.tasks.find((t) => t.id === 2)!, sprint_id: 6 });
    view({ projects: owned, sprints: withOwn });
    await userEvent.click(within(screen.getByRole("group", { name: "View" })).getByRole("button", { name: "Backlog" }));
    // Offsite's task sits in the Backlog: its select lists Offsite's pair (ids 5 and 6), not the team's.
    const select = screen.getByRole("combobox", { name: "Sprint of Book the venue for the offsite" }) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["", "5", "6"]);
    await userEvent.selectOptions(select, "6");
    expect(mocks.update).toHaveBeenCalledWith(2, { sprint_id: 6 });
    // Client Portal's follows the team's.
    const portal = screen.getByRole("combobox", { name: "Sprint of Review the copy on the portal" }) as HTMLSelectElement;
    expect(Array.from(portal.options).map((o) => o.value)).toEqual(["", "1", "2"]);
    // The drawer lists the same.
    await userEvent.click(screen.getByText("Book the venue for the offsite"));
    const sprint = within(screen.getByRole("dialog")).getByLabelText("Sprint") as HTMLSelectElement;
    expect(Array.from(sprint.options).map((o) => o.value)).toEqual(["", "5", "6"]);
  });
});

describe("the drawer's Sprint row", () => {
  const props = { people: board.people, today: "2026-11-04", onChange: vi.fn(), onDelete: vi.fn(), onClose: vi.fn() };

  it("comes after Project and before Assigned, and is left out without sprints", () => {
    const { unmount } = render(<TaskDrawer {...props} task={byTitle("Finish the portal checkout")} projects={projects} sprints={book} />);
    const labels = Array.from(document.querySelectorAll(".td-grid > label, .td-grid > span:nth-child(odd)")).map((n) => n.textContent);
    expect(labels).toEqual(["Status", "Assignee", "Due", "Milestone", "Project", "Sprint", "Assigned"]);
    expect((screen.getByLabelText("Sprint") as HTMLSelectElement).value).toBe("1");
    unmount();
    render(<TaskDrawer {...props} task={byTitle("Finish the portal checkout")} projects={projects} sprints={null} />);
    expect(screen.queryByLabelText("Sprint")).toBeNull();
  });

  it("is switched off when the person may not plan the task", () => {
    render(<TaskDrawer {...props} task={{ ...byTitle("Finish the portal checkout"), can_edit: false }} sprints={book} />);
    expect((screen.getByLabelText("Sprint") as HTMLSelectElement).disabled).toBe(true);
  });

  it("shows the closed sprint a done task was left in", () => {
    const left = { ...byTitle("Finish the portal checkout"), sprint_id: 1, status: "done" as const, project_id: null };
    render(<TaskDrawer {...props} task={left} sprints={{ team: afterClose, own: {} }} />);
    const select = screen.getByLabelText("Sprint") as HTMLSelectElement;
    expect(select.value).toBe("1");
    expect(select.selectedOptions[0].text).toBe("Sprint 1 (closed)");
  });

  it("is read on the meeting page too, when the first row is opened", async () => {
    sprintApi.list.mockResolvedValue(team);
    listing.projects.mockResolvedValue({ projects, can_manage: true });
    listing.milestones.mockResolvedValue({ milestones: [] });
    mocks.list.mockResolvedValue(board);
    render(<ActionTasks initial={[byTitle("Finish the portal checkout")]} />);
    await userEvent.click(screen.getByText("Finish the portal checkout"));
    expect(await screen.findByLabelText("Sprint")).toBeTruthy();
  });
});
