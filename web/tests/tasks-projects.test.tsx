// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ActionTasks } from "@/components/app/action-tasks";
import { TaskDrawer } from "@/components/app/task-drawer";
import { TasksView } from "@/components/app/tasks-view";
import type { MilestoneList, ProjectList, Task, TaskList } from "@/lib/api/types";
import { projectsBody } from "./helpers";

// W4 on the Tasks screen, fed the bodies captured from the running API
// (docs/fe-captures/projects-w4/). Capture 30 is Dan's board after the real extraction run: task 1
// "Finish the portal checkout" and 3 "Review the copy on the portal" in Client Portal (the third a
// guess, "Check project"), task 2 in Offsite.

const mocks = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() }));
const listing = vi.hoisted(() => ({ projects: vi.fn(), milestones: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/browser", () => ({
  api: { tasks: mocks, projects: { list: listing.projects }, milestones: { list: listing.milestones } },
}));

const board = projectsBody<TaskList>("30-tasks-filed.json");
const projects = projectsBody<ProjectList>("13-projects-list.json").projects;
const milestones = projectsBody<MilestoneList>("46-milestones-after.json").milestones; // Client Portal launch, 8 Dec
const [, , offsite] = projects;
const byTitle = (title: string): Task => board.tasks.find((t) => t.title === title)!;
const withLaunch: TaskList = {
  ...board,
  tasks: board.tasks.map((t) =>
    t.title === "Finish the portal checkout" ? { ...t, milestone_id: milestones[0].id, status: "done" } : t.title === "Review the copy on the portal" ? { ...t, milestone_id: milestones[0].id } : t,
  ),
};

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-03T12:00:00Z"));
  Object.values(mocks).forEach((fn) => fn.mockReset());
  Object.values(listing).forEach((fn) => fn.mockReset());
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the project tabs", () => {
  it("draw All projects and one tab per project, and filter the board", async () => {
    render(<TasksView initial={board} projects={projects} milestones={milestones} />);
    const tabs = within(screen.getByRole("group", { name: "Project" }));
    expect(tabs.getAllByRole("button").map((b) => b.textContent)).toEqual(["All projects", "Untitled project", "Client Portal", "Offsite"]);
    await userEvent.click(tabs.getByRole("button", { name: "Client Portal" }));
    expect(screen.getByText("Finish the portal checkout")).toBeTruthy();
    expect(screen.getByText("Review the copy on the portal")).toBeTruthy();
    expect(screen.queryByText("Book the venue for the offsite")).toBeNull();
    await userEvent.click(tabs.getByRole("button", { name: "Offsite" }));
    expect(screen.queryByText("Finish the portal checkout")).toBeNull();
    expect(screen.getByText("Book the venue for the offsite")).toBeTruthy();
  });

  it("are left out when the projects could not be read or there are none", () => {
    const { unmount } = render(<TasksView initial={board} projects={null} />);
    expect(screen.queryByRole("group", { name: "Project" })).toBeNull();
    expect(screen.queryByText("No project")).toBeNull();
    unmount();
    render(<TasksView initial={board} projects={[]} />);
    expect(screen.queryByRole("group", { name: "Project" })).toBeNull();
  });

  it("put a project badge on every card under All projects (and none inside one project), plus Check project", async () => {
    render(<TasksView initial={board} projects={projects} milestones={milestones} />);
    const card = screen.getByText("Review the copy on the portal").closest(".tk-card") as HTMLElement;
    expect(within(card).getByText("Client Portal")).toBeTruthy();
    expect(within(card).getByText("Check project")).toBeTruthy();
    const other = screen.getByText("Finish the portal checkout").closest(".tk-card") as HTMLElement;
    expect(within(other).queryByText("Check project")).toBeNull();
    await userEvent.click(within(screen.getByRole("group", { name: "Project" })).getByRole("button", { name: "Client Portal" }));
    const inside = screen.getByText("Review the copy on the portal").closest(".tk-card") as HTMLElement;
    expect(within(inside).queryByText("Client Portal")).toBeNull();
    expect(within(inside).getByText("Check project")).toBeTruthy();
  });

  it("shows 'No project' on a task that has none", () => {
    const loose: TaskList = { ...board, tasks: board.tasks.map((t, i) => (i === 0 ? { ...t, project_id: null, check_project: false } : t)) };
    render(<TasksView initial={loose} projects={projects} />);
    expect(screen.getByText("No project")).toBeTruthy();
  });

  it("counts a project check in the banner and Review opens that task", async () => {
    render(<TasksView initial={board} projects={projects} milestones={milestones} />);
    expect(screen.getByText("1 task needs attention: 1 needs its project checked")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(screen.getByRole("dialog", { name: "Task" })).toBeTruthy();
    expect(screen.getByText(/Filed under Client Portal from the meeting it came from/)).toBeTruthy();
  });

  it("joins the owner and project counts in one banner line", () => {
    const both: TaskList = { ...board, tasks: board.tasks.map((t) => (t.title === "Finish the portal checkout" ? { ...t, assignee: null } : t)) };
    render(<TasksView initial={both} projects={projects} />);
    expect(screen.getByText("2 tasks need attention: 1 needs an owner, 1 needs its project checked")).toBeTruthy();
  });

  it("files a new task under the open tab", async () => {
    mocks.create.mockResolvedValue({ ...byTitle("Book the venue for the offsite"), id: 99, title: "", project_id: offsite.id });
    render(<TasksView initial={board} projects={projects} />);
    await userEvent.click(screen.getByRole("button", { name: "New task" }));
    expect(mocks.create).toHaveBeenLastCalledWith({});
    await screen.findByRole("dialog", { name: "Task" });
    cleanup();
    render(<TasksView initial={board} projects={projects} />);
    await userEvent.click(within(screen.getByRole("group", { name: "Project" })).getByRole("button", { name: "Offsite" }));
    await userEvent.click(screen.getByRole("button", { name: "New task" }));
    expect(mocks.create).toHaveBeenLastCalledWith({ project_id: offsite.id });
  });
});

describe("the milestone card", () => {
  it("shows days left, the name and date, progress and where it was set", () => {
    render(<TasksView initial={withLaunch} projects={projects} milestones={milestones} />);
    expect(screen.getByText("35")).toBeTruthy(); // 3 Nov to 8 Dec
    expect(screen.getByText("days left")).toBeTruthy();
    expect(screen.getByText("Client Portal launch")).toBeTruthy();
    expect(screen.getByText("Tue 8 Dec 2026")).toBeTruthy();
    expect(screen.getByText("1 of 2 launch tasks done · Set in Portal planning, 3 Nov")).toBeTruthy();
    expect((screen.getByLabelText("1 of 2 launch tasks done") as HTMLProgressElement).value).toBe(50);
  });

  it("filters to the launch tasks and back", async () => {
    render(<TasksView initial={withLaunch} projects={projects} milestones={milestones} />);
    await userEvent.click(screen.getByRole("button", { name: "Show launch tasks" }));
    expect(screen.queryByText("Book the venue for the offsite")).toBeNull();
    expect(screen.getByText("Finish the portal checkout")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Show all tasks" }));
    expect(screen.getByText("Book the venue for the offsite")).toBeTruthy();
  });

  it("belongs to its project's tab only, and is not drawn once the date has passed", async () => {
    render(<TasksView initial={withLaunch} projects={projects} milestones={milestones} />);
    await userEvent.click(within(screen.getByRole("group", { name: "Project" })).getByRole("button", { name: "Offsite" }));
    expect(screen.queryByText("Client Portal launch")).toBeNull();
    cleanup();
    render(<TasksView initial={withLaunch} projects={projects} milestones={[{ ...milestones[0], due_date: "2026-10-01" }]} />);
    expect(screen.queryByText("days left")).toBeNull();
  });
});

describe("the task drawer's project rows", () => {
  const setup = (task: Task, extra: Partial<Parameters<typeof TaskDrawer>[0]> = {}) => {
    const props = { task, people: board.people, today: "2026-11-03", projects, milestones, onChange: vi.fn(), onDelete: vi.fn(), onClose: vi.fn(), ...extra };
    render(<TaskDrawer {...props} />);
    return props;
  };

  it("draws Milestone and Project selects with None and No project", () => {
    setup(byTitle("Finish the portal checkout"));
    const milestone = screen.getByLabelText("Milestone") as HTMLSelectElement;
    expect([...milestone.options].map((o) => o.text)).toEqual(["None", "Client Portal launch, 8 Dec"]);
    const project = screen.getByLabelText("Project") as HTMLSelectElement;
    expect([...project.options].map((o) => o.text)).toEqual(["No project", "Untitled project", "Client Portal", "Offsite"]);
    expect(project.selectedOptions[0].text).toBe("Client Portal");
  });

  it("sends only the project when it is changed, and only the milestone when that is", async () => {
    const task = byTitle("Finish the portal checkout");
    const props = setup(task);
    mocks.update.mockResolvedValue({ ...task, project_id: offsite.id });
    await userEvent.selectOptions(screen.getByLabelText("Project"), "Offsite");
    expect(mocks.update).toHaveBeenLastCalledWith(task.id, { project_id: offsite.id });
    await waitFor(() => expect(props.onChange).toHaveBeenCalled());
    mocks.update.mockResolvedValue({ ...task, milestone_id: milestones[0].id });
    await userEvent.selectOptions(screen.getByLabelText("Milestone"), "Client Portal launch, 8 Dec");
    expect(mocks.update).toHaveBeenLastCalledWith(task.id, { milestone_id: milestones[0].id });
  });

  it("shows the Check project line with Confirm, which sends check_project false", async () => {
    const task = byTitle("Review the copy on the portal");
    const props = setup(task);
    expect(screen.getByText(/Filed under Client Portal from the meeting it came from, but it could belong elsewhere\. Confirm or pick another project below\./)).toBeTruthy();
    mocks.update.mockResolvedValue({ ...task, check_project: false });
    await userEvent.click(screen.getByRole("button", { name: "Confirm Client Portal" }));
    expect(mocks.update).toHaveBeenCalledWith(task.id, { check_project: false });
    await waitFor(() => expect(props.onChange).toHaveBeenCalledWith(expect.objectContaining({ check_project: false })));
  });

  it("draws no Check project line for a task that was filed with confidence", () => {
    setup(byTitle("Finish the portal checkout"));
    expect(screen.queryByText(/but it could belong elsewhere/)).toBeNull();
  });

  it("switches the rows off for someone who may not change the task", () => {
    setup({ ...byTitle("Review the copy on the portal"), can_edit: false });
    expect((screen.getByLabelText("Project") as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByLabelText("Milestone") as HTMLSelectElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: /^Confirm / })).toBeNull();
  });
});

describe("the meeting page's drawer", () => {
  it("gets the project rows once the lists are read when a row is opened", async () => {
    mocks.list.mockResolvedValue(board);
    listing.projects.mockResolvedValue({ projects, can_manage: true });
    listing.milestones.mockResolvedValue({ milestones });
    render(<ActionTasks initial={board.tasks} />);
    await userEvent.click(screen.getByText("Review the copy on the portal"));
    await waitFor(() => expect(screen.getByLabelText("Project")).toBeTruthy());
    expect((screen.getByLabelText("Project") as HTMLSelectElement).selectedOptions[0].text).toBe("Client Portal");
  });
});
