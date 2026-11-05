// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TaskDrawer } from "@/components/app/task-drawer";
import { ApiError } from "@/lib/api/client";
import type { Task, TaskList } from "@/lib/api/types";
import { tasksBody } from "./helpers";

const mocks = vi.hoisted(() => ({ update: vi.fn(), remove: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { tasks: mocks } }));

// Chidi's list (capture 32): a member sees the task given to him from a private meeting.
const owners = tasksBody<TaskList>("17-tasks.json");
const mine = tasksBody<TaskList>("32-chidi-tasks.json");
const base = owners.tasks[0];
const TODAY = "2026-11-03";

const setup = (task: Task, extra: Partial<Parameters<typeof TaskDrawer>[0]> = {}) => {
  const props = { task, people: owners.people, today: TODAY, onChange: vi.fn(), onDelete: vi.fn(), onClose: vi.fn(), ...extra };
  render(<TaskDrawer {...props} />);
  return props;
};

beforeEach(() => Object.values(mocks).forEach((fn) => fn.mockReset()));
afterEach(cleanup);

describe("the task drawer", () => {
  it("shows the title, Status, Assignee, Due, Assigned and the quote with a link to the meeting", () => {
    setup(base);
    expect((screen.getByLabelText("Task title") as HTMLTextAreaElement).value).toBe(base.title);
    expect((screen.getByLabelText("Status") as HTMLSelectElement).value).toBe("todo");
    expect((screen.getByLabelText("Assignee") as HTMLSelectElement).selectedOptions[0].text).toBe("Chidi Eze");
    expect((screen.getByLabelText("Due") as HTMLSelectElement).selectedOptions[0].text).toBe("Tomorrow");
    expect(screen.getByText("Today, Tue 3 Nov")).toBeTruthy();
    expect(screen.getByText("FROM THE MEETING")).toBeTruthy();
    expect(screen.getByText(base.quote!)).toBeTruthy();
    expect(screen.getByRole("link", { name: /Google Meet nat-aaaa-bbb, 3 Nov/ }).getAttribute("href")).toBe("/meetings/1");
  });

  it("draws no Milestone, Project or Sprint rows: they need W4 and W9", () => {
    setup(base);
    for (const row of ["Milestone", "Project", "Sprint"]) expect(screen.queryByText(row)).toBeNull();
    expect(screen.queryByText(/Invite them to the team/)).toBeNull();
  });

  it("lists Unassigned and the team in Assignee, marking the person themselves", () => {
    setup(base);
    const options = [...(screen.getByLabelText("Assignee") as HTMLSelectElement).options].map((o) => o.text);
    expect(options).toEqual(["Unassigned", "Dan Okoro (you)", "Chidi Eze", "Amina Bello"]);
  });

  it("sends only the field that changed, and shows the answer", async () => {
    const props = setup(base);
    mocks.update.mockResolvedValue({ ...base, status: "doing", is_new: false });
    await userEvent.selectOptions(screen.getByLabelText("Status"), "doing");
    expect(mocks.update).toHaveBeenCalledWith(base.id, { status: "doing" });
    await waitFor(() => expect(props.onChange).toHaveBeenCalledWith(expect.objectContaining({ status: "doing" })));
  });

  it("clears the due date and sets a new assignee", async () => {
    setup(base);
    mocks.update.mockResolvedValue(base);
    await userEvent.selectOptions(screen.getByLabelText("Due"), "");
    expect(mocks.update).toHaveBeenCalledWith(base.id, { due_date: null });
    await userEvent.selectOptions(screen.getByLabelText("Assignee"), "Amina Bello");
    expect(mocks.update).toHaveBeenCalledWith(base.id, { assignee_user_id: 4 });
  });

  it("saves a new title when the field is left, and not when it did not change", async () => {
    setup(base);
    mocks.update.mockResolvedValue(base);
    const title = screen.getByLabelText("Task title");
    await userEvent.click(title);
    await userEvent.tab();
    expect(mocks.update).not.toHaveBeenCalled();
    await userEvent.type(title, " today");
    await userEvent.tab();
    expect(mocks.update).toHaveBeenCalledWith(base.id, { title: `${base.title} today` });
  });

  it("says what the API said when it refuses, and puts the title back", async () => {
    setup(base);
    mocks.update.mockRejectedValue(new ApiError(403, "team_forbidden", "You can't do that to this task. Ask an owner or admin of your team."));
    await userEvent.selectOptions(screen.getByLabelText("Status"), "done");
    expect(await screen.findByText(/You can't do that to this task/)).toBeTruthy();
  });

  it("disables what the person may not change, from the flags the API sent", () => {
    const forbidden: Task = { ...base, can_update: false, can_edit: false, can_assign: false, can_claim: false, can_delete: false };
    setup(forbidden);
    expect((screen.getByLabelText("Status") as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByLabelText("Assignee") as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByLabelText("Due") as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByLabelText("Task title") as HTMLTextAreaElement).readOnly).toBe(true);
    expect(screen.queryByRole("button", { name: "Delete task" })).toBeNull();
  });

  it("offers a member who can only take a task Unassigned and themselves", () => {
    const free: Task = { ...base, assignee: null, can_assign: false, can_claim: true };
    setup(free);
    const options = [...(screen.getByLabelText("Assignee") as HTMLSelectElement).options].map((o) => o.text);
    expect(options).toEqual(["Unassigned", "Dan Okoro (you)"]);
  });

  it("names a spoken owner nobody on the team matched; a member is told to ask an owner or admin", () => {
    setup({ ...base, assignee: null, spoken_owner: "Kemi Adeyemi" });
    expect(screen.getByText('"Kemi Adeyemi" was named as the owner in the meeting, but is not on your team.')).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Invite/ })).toBeNull();
    expect(screen.getByText("Ask an owner or admin of your team to invite them.")).toBeTruthy();
  });

  it("offers Invite them to the team to an owner or admin, with the spoken name", async () => {
    const onInvite = vi.fn();
    setup({ ...base, assignee: null, spoken_owner: "Kemi Adeyemi" }, { canInvite: true, onInvite });
    await userEvent.click(screen.getByRole("button", { name: "Invite them to the team" }));
    expect(onInvite).toHaveBeenCalledWith("Kemi Adeyemi");
  });

  it("shows the quote but no meeting link when the meeting is private to someone else", () => {
    const given = mine.tasks.find((t) => t.meeting === null)!;
    setup(given);
    expect(screen.getByText("FROM THE MEETING")).toBeTruthy();
    expect(screen.getByText(given.quote!)).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("deletes the task and tells the page", async () => {
    const props = setup(base);
    mocks.remove.mockResolvedValue(undefined);
    await userEvent.click(screen.getByRole("button", { name: "Delete task" }));
    expect(mocks.remove).toHaveBeenCalledWith(base.id);
    await waitFor(() => expect(props.onDelete).toHaveBeenCalledWith(base.id));
  });

  it("closes with Done, the close button, a click outside and Escape", async () => {
    const props = setup(base);
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    await userEvent.keyboard("{Escape}");
    expect(props.onClose).toHaveBeenCalledTimes(3);
  });
});
