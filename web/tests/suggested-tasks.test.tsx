// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ActionTasks } from "@/components/app/action-tasks";
import { SettingsPlannedSections } from "@/components/app/settings-planned-sections";
import { TaskDrawer } from "@/components/app/task-drawer";
import { TasksView } from "@/components/app/tasks-view";
import { ApiError } from "@/lib/api/client";
import type { MeetingTasks, Task, TaskList, TasksSettings } from "@/lib/api/types";
import { actionSummary, confidenceLabel, suggestedLead, transcriptHref } from "@/lib/tasks";
import { suggestedBody } from "./helpers";

// Suggested tasks: the Tasks screen's section, the drawer box and evidence card, the meeting page's
// "To confirm" pill and summary tail, and the Settings row. Every body is a capture from the running
// API (docs/fe-captures/suggested-tasks/, docs/fe-integration-guide-suggested-tasks.md); the model's
// reply behind them is a labelled stand-in.

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn(),
  accept: vi.fn(),
  get: vi.fn(),
  setSettings: vi.fn(),
}));
const nav = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: nav.push }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    tasks: { list: mocks.list, create: mocks.create, update: mocks.update, remove: mocks.remove, accept: mocks.accept, get: mocks.get },
    settings: { tasks: { set: mocks.setSettings } },
  },
}));

const board = suggestedBody<TaskList>("12-board.json");
const waiting = suggestedBody<TaskList>("13-suggested-list.json").tasks; // invoice, demo, analytics
const accepted = suggestedBody<Task>("30-accept-amina.json");
const privateOne = suggestedBody<Task>("20-chidi-sees-his-suggestion.json");
const meeting = suggestedBody<MeetingTasks>("15-meeting-tasks.json");
const [invoice, demo, analytics] = waiting;
const stored = suggestedBody<TasksSettings>("09-settings-default.json");

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-08T12:00:00Z"));
  Object.values(mocks).forEach((fn) => fn.mockReset());
  mocks.list.mockResolvedValue({ ...board, tasks: [] });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const section = () => screen.getByRole("region", { name: "Suggested tasks" });
const row = (title: string) => within(section()).getByText(title).closest(".tk-sg-row") as HTMLElement;

describe("the Suggested tasks section", () => {
  it("is not drawn when nothing waits, or when the list could not be read", () => {
    const { unmount } = render(<TasksView initial={board} suggested={[]} />);
    expect(screen.queryByText("Suggested tasks")).toBeNull();
    unmount();
    render(<TasksView initial={board} suggested={null} />);
    expect(screen.queryByText("Suggested tasks")).toBeNull();
    expect(screen.getByRole("region", { name: "To do" })).toBeTruthy(); // the board is unaffected
  });

  it("has the designer's heading, the count in amber and the sub-line", () => {
    render(<TasksView initial={board} suggested={waiting} />);
    expect(within(section()).getByText("Suggested tasks")).toBeTruthy();
    expect(within(section()).getByText("3 to confirm")).toBeTruthy();
    expect(
      within(section()).getByText(
        "These action items were not clear enough to add on their own. Check where each one came from, then accept or dismiss it.",
      ),
    ).toBeTruthy();
  });

  it("draws each row: the title, the confidence pill, the evidence, the reason, the owner and four buttons", () => {
    render(<TasksView initial={board} suggested={waiting} />);
    const a = row(analytics.title);
    expect(within(a).getByText("Medium confidence")).toBeTruthy();
    expect(within(a).getByText("Amina")).toBeTruthy(); // who said it
    expect(within(a).getByText(`“${analytics.quote!}”`)).toBeTruthy();
    expect(within(a).getByText(analytics.reason!)).toBeTruthy();
    expect(within(a).getByText("Amina Bello")).toBeTruthy(); // the assignee
    expect(within(a).getAllByRole("button").map((b) => b.textContent)).toEqual(["Edit", "Dismiss", "Accept"]);
    expect(within(a).getByRole("link", { name: "Show source" }).getAttribute("href")).toBe("/meetings/1?tab=transcript&line=3");
    expect(within(row(demo.title)).getByText("Low confidence")).toBeTruthy();
  });

  it("shows no time and no paraphrase line in the evidence, and no reason line when there is none", () => {
    render(<TasksView initial={board} suggested={waiting} />);
    const i = row(invoice.title);
    expect(within(i).getByText("No owner")).toBeTruthy();
    expect(invoice.reason).toBeNull();
    expect(i.querySelector(".tk-sg-reason")).toBeNull();
    expect(i.querySelector(".tk-sg-ev")?.textContent).toBe(`Dan“${invoice.quote!}”`); // speaker then quote, nothing else
  });

  it("opens Show source at the transcript row of the quote", () => {
    render(<TasksView initial={board} suggested={waiting} />);
    expect(within(row(demo.title)).getByRole("link", { name: "Show source" }).getAttribute("href")).toBe(
      `/meetings/1?tab=transcript&line=${demo.quote_line}`,
    );
  });

  it("accepts: the row leaves, the task joins the board and the toast says so", async () => {
    mocks.accept.mockResolvedValue({ ...analytics, suggested: false });
    render(<TasksView initial={board} suggested={waiting} />);
    await userEvent.click(within(row(analytics.title)).getByRole("button", { name: "Accept" }));
    expect(mocks.accept).toHaveBeenCalledWith(analytics.id);
    await waitFor(() => expect(within(section()).queryByText(analytics.title)).toBeNull());
    expect(screen.getByText("Task added to the board")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "To do" })).getByText(analytics.title)).toBeTruthy();
    expect(within(section()).getByText("2 to confirm")).toBeTruthy();
  });

  it("dismisses: the row leaves and the toast says so", async () => {
    mocks.remove.mockResolvedValue(undefined);
    render(<TasksView initial={board} suggested={waiting} />);
    await userEvent.click(within(row(demo.title)).getByRole("button", { name: "Dismiss" }));
    expect(mocks.remove).toHaveBeenCalledWith(demo.id);
    await waitFor(() => expect(within(section()).queryByText(demo.title)).toBeNull());
    expect(screen.getByText("Suggestion dismissed")).toBeTruthy();
  });

  it("says it could not update the task and keeps the row when Accept or Dismiss fails", async () => {
    mocks.accept.mockRejectedValue(new ApiError(500, "server_error", "Something went wrong on our side."));
    mocks.remove.mockRejectedValue(new TypeError("Failed to fetch"));
    render(<TasksView initial={board} suggested={waiting} />);
    await userEvent.click(within(row(analytics.title)).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(screen.getByText("Could not update the task. Try again.")).toBeTruthy());
    expect(within(section()).getByText(analytics.title)).toBeTruthy();
    await userEvent.click(within(row(demo.title)).getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(mocks.remove).toHaveBeenCalled());
    expect(within(section()).getByText(demo.title)).toBeTruthy();
    expect(within(section()).getByText("3 to confirm")).toBeTruthy();
  });

  it("drops the row when the task is gone (404): someone dismissed it elsewhere", async () => {
    mocks.accept.mockRejectedValue(new ApiError(404, "not_found", "No such task"));
    render(<TasksView initial={board} suggested={waiting} />);
    await userEvent.click(within(row(analytics.title)).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(within(section()).queryByText(analytics.title)).toBeNull());
    expect(screen.getByText("Could not update the task. Try again.")).toBeTruthy();
  });

  it("disables Accept and Dismiss for someone who may not edit the task, and has no Show source without the meeting", () => {
    render(<TasksView initial={board} suggested={[privateOne]} />);
    const r = row(privateOne.title);
    expect((within(r).getByRole("button", { name: "Accept" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(r).getByRole("button", { name: "Dismiss" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(r).getByRole("button", { name: "Edit" }) as HTMLButtonElement).disabled).toBe(false);
    expect(within(r).queryByRole("link", { name: "Show source" })).toBeNull();
    expect(within(r).queryByText("Chidi")).toBeNull(); // no speaker: the meeting is not theirs to read
  });

  it("lets the person who owns the meeting accept and dismiss even without edit rights (can_decide)", async () => {
    mocks.accept.mockResolvedValue({ ...analytics, suggested: false });
    render(<TasksView initial={board} suggested={[{ ...analytics, can_edit: false, can_delete: false, can_decide: true }]} />);
    const r = row(analytics.title);
    expect((within(r).getByRole("button", { name: "Dismiss" }) as HTMLButtonElement).disabled).toBe(false);
    await userEvent.click(within(r).getByRole("button", { name: "Accept" }));
    expect(mocks.accept).toHaveBeenCalledWith(analytics.id);
  });

  it("Edit opens the drawer with the amber box, and an edit leaves the task suggested", async () => {
    mocks.update.mockResolvedValue({ ...analytics, title: "Look into analytics" });
    render(<TasksView initial={board} suggested={waiting} />);
    await userEvent.click(within(row(analytics.title)).getByRole("button", { name: "Edit" }));
    const drawer = screen.getByRole("dialog", { name: "Task" });
    expect(within(drawer).getByText("Suggested task, medium confidence.")).toBeTruthy();
    expect(within(drawer).getByText(new RegExp(analytics.reason!.slice(0, 20)))).toBeTruthy();
    const title = within(drawer).getByLabelText("Task title");
    await userEvent.clear(title);
    await userEvent.type(title, "Look into analytics");
    await userEvent.tab();
    await waitFor(() => expect(mocks.update).toHaveBeenCalledWith(analytics.id, { title: "Look into analytics" }));
    expect(within(drawer).getByText("Suggested task, medium confidence.")).toBeTruthy(); // still suggested
    expect(within(section()).getByText("Look into analytics")).toBeTruthy();
  });

  it("accepts from the drawer: the box goes and the task is on the board", async () => {
    mocks.accept.mockResolvedValue({ ...analytics, suggested: false });
    render(<TasksView initial={board} suggested={waiting} />);
    await userEvent.click(within(row(analytics.title)).getByRole("button", { name: "Edit" }));
    const drawer = screen.getByRole("dialog", { name: "Task" });
    await userEvent.click(within(drawer).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(within(drawer).queryByText("Suggested task, medium confidence.")).toBeNull());
    expect(screen.getByText("Task added to the board")).toBeTruthy();
    expect(within(section() ?? document.body).queryByText(analytics.title)).toBeNull();
  });

  it("dismisses from the drawer: it closes and the row is gone", async () => {
    mocks.remove.mockResolvedValue(undefined);
    render(<TasksView initial={board} suggested={waiting} />);
    await userEvent.click(within(row(demo.title)).getByRole("button", { name: "Edit" }));
    await userEvent.click(within(screen.getByRole("dialog", { name: "Task" })).getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Task" })).toBeNull());
    expect(screen.getByText("Suggestion dismissed")).toBeTruthy();
    expect(within(section()).queryByText(demo.title)).toBeNull();
  });
});

describe("the task drawer's evidence card", () => {
  const setup = (task: Task) =>
    render(<TaskDrawer task={task} people={board.people} today="2026-11-08" onChange={vi.fn()} onDelete={vi.fn()} onClose={vi.fn()} />);

  it("shows who said the quote, the quote and Show in transcript, with no time", () => {
    setup(analytics);
    const card = screen.getByText(`“${analytics.quote!}”`).closest(".td-ev") as HTMLElement;
    expect(within(card).getByText("Amina")).toBeTruthy();
    expect(within(card).getByRole("link", { name: "Show in transcript" }).getAttribute("href")).toBe(
      "/meetings/1?tab=transcript&line=3",
    );
    expect(card.textContent).not.toMatch(/\d{2}:\d{2}/);
  });

  it("asks for the one task to learn who said the quote when the list did not say", async () => {
    const made = board.tasks.find((t) => t.quote && t.meeting)!;
    expect(made.quote_speaker).toBeNull(); // a Board list carries it for suggested tasks only
    mocks.get.mockResolvedValue({ ...made, quote_speaker: "Dan", quote_line: 4 });
    setup(made);
    const card = screen.getByText(`“${made.quote!}”`).closest(".td-ev") as HTMLElement;
    await waitFor(() => expect(within(card).getByText("Dan")).toBeTruthy());
    expect(mocks.get).toHaveBeenCalledWith(made.id);
    expect(within(card).getByRole("link", { name: "Show in transcript" }).getAttribute("href")).toBe(
      `/meetings/${made.meeting!.id}?tab=transcript&line=4`,
    );
  });

  it("keeps the quote without a speaker when that call fails", async () => {
    const made = board.tasks.find((t) => t.quote && t.meeting)!;
    mocks.get.mockRejectedValue(new Error("offline"));
    setup(made);
    await waitFor(() => expect(mocks.get).toHaveBeenCalled());
    const card = screen.getByText(`“${made.quote!}”`).closest(".td-ev") as HTMLElement;
    expect(card.querySelector(".td-ev-who")).toBeNull();
  });

  it("has no speaker line and no transcript link for someone who cannot read the meeting", () => {
    setup(privateOne);
    const card = screen.getByText(`“${privateOne.quote!}”`).closest(".td-ev") as HTMLElement;
    expect(within(card).queryByRole("link")).toBeNull();
    expect(card.querySelector(".td-ev-who")).toBeNull();
  });

  it("draws the amber box only for a suggested task, and with no reason says only the lead", () => {
    const { unmount } = setup({ ...accepted });
    expect(screen.queryByText(/Suggested task/)).toBeNull();
    unmount();
    setup(invoice); // a suggestion whose reason was dropped by the server's check
    expect(screen.getByText("Suggested task, low confidence.")).toBeTruthy();
  });
});

describe("the meeting page's Action items", () => {
  it("counts only the tasks that were made and ends with the tasks to confirm", () => {
    render(<ActionTasks initial={meeting.tasks} />);
    expect(screen.getByRole("link", { name: "2 tasks created, assigned to 2 people, 3 to confirm · View board" })).toBeTruthy();
    expect(meeting.assigned_people).toBe(2);
    expect(meeting.to_confirm).toBe(3);
  });

  it("puts the pill To confirm on each suggested row and not Needs owner", () => {
    render(<ActionTasks initial={meeting.tasks} />);
    expect(screen.getAllByText("To confirm")).toHaveLength(3);
    expect(screen.queryByText("Needs owner")).toBeNull(); // the unowned one is only a suggestion
  });

  it("has no tail when nothing waits", () => {
    expect(actionSummary(meeting.tasks.filter((t) => !t.suggested))).toBe("2 tasks created, assigned to 2 people");
  });

  it("accepts from the drawer and says so in a toast", async () => {
    mocks.list.mockResolvedValue({ ...board, tasks: [] });
    mocks.accept.mockResolvedValue({ ...analytics, suggested: false });
    render(<ActionTasks initial={meeting.tasks} />);
    await userEvent.click(screen.getByText(analytics.title));
    await userEvent.click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() => expect(screen.getByText("Task added to the board")).toBeTruthy());
    expect(screen.getAllByText("To confirm")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "3 tasks created, assigned to 3 people, 2 to confirm · View board" })).toBeTruthy();
  });
});

describe("Settings > Tasks: Ask before adding unclear tasks", () => {
  const card = () => screen.getByRole("heading", { name: "Tasks" }).closest("section") as HTMLElement;

  it("is the second row, between Create tasks and Email assignees, on by default, with the designer's words", () => {
    render(<SettingsPlannedSections tasks={stored} />);
    const titles = [...card().querySelectorAll(".pl-row-title")].map((n) => n.textContent);
    expect(titles.slice(0, 3)).toEqual(["Create tasks from action items", "Ask before adding unclear tasks", "Email assignees"]);
    const ask = within(card()).getByRole("switch", { name: "Ask before adding unclear tasks" });
    expect(ask.getAttribute("aria-checked")).toBe("true");
    expect(
      within(card()).getByText(
        `Clear commitments like "I'll take checkout" are added straight away. Vague or conditional ones wait under Suggested tasks.`,
      ),
    ).toBeTruthy();
  });

  it("saves all three values when it is flipped", async () => {
    mocks.setSettings.mockResolvedValue({ auto_create_tasks: true, email_assignees: true, ask_before_unclear: false });
    render(<SettingsPlannedSections tasks={stored} />);
    await userEvent.click(within(card()).getByRole("switch", { name: "Ask before adding unclear tasks" }));
    expect(mocks.setSettings).toHaveBeenCalledWith({ auto_create_tasks: true, email_assignees: true, ask_before_unclear: false });
    await waitFor(() =>
      expect(within(card()).getByRole("switch", { name: "Ask before adding unclear tasks" }).getAttribute("aria-checked")).toBe("false"),
    );
  });

  it("leaves it where it was and says so when the save fails", async () => {
    mocks.setSettings.mockRejectedValue(new ApiError(500, "server_error", "Something went wrong on our side."));
    render(<SettingsPlannedSections tasks={stored} />);
    const toggle = within(card()).getByRole("switch", { name: "Ask before adding unclear tasks" });
    await userEvent.click(toggle);
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeNull());
    expect(toggle.getAttribute("aria-checked")).toBe("true");
  });
});

describe("the helpers", () => {
  it("writes the confidence pill and the drawer's lead the designer's way", () => {
    expect(confidenceLabel("medium")).toBe("Medium confidence");
    expect(confidenceLabel("low")).toBe("Low confidence");
    expect(confidenceLabel(null)).toBe("");
    expect(suggestedLead({ confidence: "medium" })).toBe("Suggested task, medium confidence.");
    expect(suggestedLead({ confidence: null })).toBe("Suggested task.");
  });

  it("links to the transcript tab, at the row when there is one, and nowhere without the meeting", () => {
    expect(transcriptHref(analytics)).toBe("/meetings/1?tab=transcript&line=3");
    expect(transcriptHref({ ...analytics, quote_line: null })).toBe("/meetings/1?tab=transcript");
    expect(transcriptHref(privateOne)).toBeNull();
  });
});
