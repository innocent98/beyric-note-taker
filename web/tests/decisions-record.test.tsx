// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DecisionsView } from "@/components/app/decisions-view";
import { ApiError } from "@/lib/api/client";
import type { Decision, DecisionList, Task, TaskList } from "@/lib/api/types";
import { isDueThisWeek, metaLine, movedCount, revisitBanner, weekOf } from "@/lib/decisions";
import { decisionsBody, tasksBody } from "./helpers";

// The Decisions record is fed the bodies captured from the running API
// (docs/fe-captures/decisions/, cited in docs/fe-integration-guide-decisions.md).

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  hide: vi.fn(),
  update: vi.fn(),
  taskGet: vi.fn(),
  taskList: vi.fn(),
  projects: vi.fn(),
  milestones: vi.fn(),
}));
vi.mock("@/lib/api/browser", () => ({
  api: {
    decisions: { list: mocks.list, hide: mocks.hide, update: mocks.update },
    tasks: { get: mocks.taskGet, list: mocks.taskList },
    projects: { list: mocks.projects },
    milestones: { list: mocks.milestones },
    sprints: { list: vi.fn().mockRejectedValue(new Error("no")) },
  },
}));

const owner = decisionsBody<DecisionList>("11-dan-list.json");
const teammate = decisionsBody<DecisionList>("13-chidi-list.json");
const replacedView = decisionsBody<DecisionList>("24-after-replaced.json");
const dueView = decisionsBody<DecisionList>("21-after-revisit.json");
const proposedTab = decisionsBody<DecisionList>("15-tab-proposed.json");
const replacedEmpty = decisionsBody<DecisionList>("16-tab-replaced-empty.json");
const board = tasksBody<TaskList>("17-tasks.json");

const sharedMeeting = (list: DecisionList) => list.groups.find((g) => g.meeting.id === 1)!;
const flat = (list: DecisionList) => list.groups.flatMap((g) => g.decisions);
const free = flat(owner).find((d) => d.id === 1)!; // approved, Dan, a quote on line 1, one related task
const launch = flat(owner).find((d) => d.id === 2)!; // approved, nothing said about who decided
const lifetime = flat(owner).find((d) => d.id === 3)!; // proposed, Amina, a quote on line 4

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-08T12:00:00Z"));
  Object.values(mocks).forEach((fn) => fn.mockReset());
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

// A decision's words are also an option of the Replaced by selects, so the row is found by its own class.
const wordsOf = (text: string) => screen.getAllByText(text).find((el) => el.classList.contains("dec-text"))!;
const row = (text: string) => wordsOf(text).closest("button")!;
const openRow = (text: string) => userEvent.click(wordsOf(text));
const panelOf = (text: string) => row(text).parentElement!.querySelector<HTMLElement>(".dec-panel")!;

describe("tabs, counts and the banner", () => {
  it("draws All, Approved, Proposed, Replaced and Rejected with the counts of the whole log", () => {
    render(<DecisionsView initial={owner} />);
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual(["All4", "Approved3", "Proposed1", "Replaced0", "Rejected0"]);
    expect(screen.getByRole("tab", { name: /^All/ }).getAttribute("aria-selected")).toBe("true");
  });

  it("asks the API for one status when a tab is picked, and keeps the counts the answer gives", async () => {
    mocks.list.mockResolvedValue(proposedTab);
    render(<DecisionsView initial={owner} />);
    await userEvent.click(screen.getByRole("tab", { name: /^Proposed/ }));
    expect(mocks.list).toHaveBeenCalledWith({ q: undefined, status: "proposed" });
    expect(await screen.findByText(lifetime.text)).toBeTruthy();
    expect(screen.queryByText(free.text)).toBeNull();
    expect(screen.getByRole("tab", { name: /^All/ }).textContent).toBe("All4"); // counts: the whole log
    expect(screen.getByRole("tab", { name: /^Proposed/ }).getAttribute("aria-selected")).toBe("true");
  });

  it("says No decisions match. for a tab with nothing in it (the log is not empty)", async () => {
    mocks.list.mockResolvedValue(replacedEmpty);
    render(<DecisionsView initial={owner} />);
    await userEvent.click(screen.getByRole("tab", { name: /^Replaced/ }));
    expect(await screen.findByText("No decisions match.")).toBeTruthy();
    expect(screen.queryByText("No decisions yet.")).toBeNull();
    expect(screen.getAllByRole("tab")).toHaveLength(5); // the tabs stay so another can be picked
  });

  it("shows no tabs, search or banner, only the empty box, when the log is empty", () => {
    render(
      <DecisionsView
        initial={{ groups: [], has_more: false, next_before_id: null, counts: { all: 0, approved: 0, proposed: 0, replaced: 0, rejected: 0 }, due_this_week: 0 }}
      />,
    );
    expect(screen.getByText("No decisions yet.")).toBeTruthy();
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.queryByRole("searchbox")).toBeNull();
  });

  it("shows the amber banner only when something is due this week, with the designer's words", () => {
    const { unmount } = render(<DecisionsView initial={owner} />);
    expect(screen.queryByText(/due to be revisited this week/)).toBeNull();
    unmount();
    render(<DecisionsView initial={dueView} />);
    expect(screen.getByText("1 decision is due to be revisited this week.")).toBeTruthy();
    expect(revisitBanner(2)).toBe("2 decisions are due to be revisited this week.");
  });
});

describe("a row", () => {
  it("shows the status pill and the meta line: who decided, related tasks, the revisit day", () => {
    render(<DecisionsView initial={owner} />);
    const first = row(free.text);
    expect(within(first).getByText("Approved")).toBeTruthy();
    expect(first.textContent).toContain("Decided by Dan · 1 related task");
    expect(within(row(lifetime.text)).getByText("Proposed")).toBeTruthy();
    // a decision nobody could be named for has no "Decided by" and no empty meta line
    expect(row(launch.text).textContent).toContain("1 related task");
    expect(row(launch.text).textContent).not.toContain("Decided by");
    expect(metaLine({ decided_by: null, related_tasks: [], revisit_on: "2026-11-30" })).toBe("Revisit Mon 30 Nov 2026");
    expect(metaLine({ decided_by: null, related_tasks: [], revisit_on: null })).toBe("");
  });

  it("greys out a replaced or a rejected decision", () => {
    render(<DecisionsView initial={replacedView} />);
    const rejected = wordsOf(free.text);
    expect(rejected.className).toContain("dec-text-off");
    expect(wordsOf(lifetime.text).className).toContain("dec-text-off");
    expect(wordsOf(launch.text).className).not.toContain("dec-text-off");
  });

  it("opens with Status, Revisit on, Decided by, the evidence with its link, and the related tasks", async () => {
    render(<DecisionsView initial={owner} />);
    await openRow(free.text);
    const panel = within(panelOf(free.text));
    expect((panel.getByLabelText("Status") as HTMLSelectElement).value).toBe("approved");
    expect(panel.getAllByRole("option").map((o) => o.textContent)).toEqual(["Approved", "Proposed", "Replaced", "Rejected"]);
    expect((panel.getByLabelText("Revisit on") as HTMLInputElement).type).toBe("date");
    expect(panel.getByText("Decided by")).toBeTruthy();
    expect(panel.getByText("“I think we should keep the free plan for two more months.”")).toBeTruthy();
    expect(panel.getByRole("link", { name: "Show in transcript" }).getAttribute("href")).toBe("/meetings/1?tab=transcript&line=1");
    expect(panel.getByText("Related tasks")).toBeTruthy();
    expect(panel.getByRole("button", { name: /Update the pricing page/ }).textContent).toContain("To do");
    expect(panel.getByRole("button", { name: "Remove from log" })).toBeTruthy();
  });

  it("says No transcript is available for this meeting. when there is no quote, and no Decided by when nobody was named", async () => {
    render(<DecisionsView initial={owner} />);
    await openRow(launch.text);
    const panel = within(panelOf(launch.text));
    expect(panel.getByText("No transcript is available for this meeting.")).toBeTruthy();
    expect(panel.queryByRole("link", { name: "Show in transcript" })).toBeNull();
    expect(panel.queryByText("Decided by")).toBeNull();
  });

  it("draws the quote without a link when its row could not be found again", async () => {
    const lost = { ...owner, groups: [{ ...sharedMeeting(owner), decisions: [{ ...free, line: null, quote_speaker: null }] }] };
    render(<DecisionsView initial={lost} />);
    await openRow(free.text);
    const panel = within(panelOf(free.text));
    expect(panel.getByText("“I think we should keep the free plan for two more months.”")).toBeTruthy();
    expect(panel.queryByRole("link", { name: "Show in transcript" })).toBeNull();
    expect(panel.getAllByText("Dan")).toHaveLength(2); // the speaker falls back to the one who decided
  });
});

describe("the owner changes a decision", () => {
  it("changes the status with one PATCH, shows the toast, and moves the pill and the counts", async () => {
    mocks.update.mockResolvedValue(decisionsBody<Decision>("19-patch-status.json"));
    render(<DecisionsView initial={owner} />);
    await openRow(free.text);
    await userEvent.selectOptions(within(panelOf(free.text)).getByLabelText("Status"), "rejected");
    expect(mocks.update).toHaveBeenCalledWith(1, { status: "rejected" });
    expect(await screen.findByText("Status changed to Rejected")).toBeTruthy();
    expect(within(row(free.text)).getByText("Rejected")).toBeTruthy();
    expect(screen.getByRole("tab", { name: /^Approved/ }).textContent).toBe("Approved2");
    expect(screen.getByRole("tab", { name: /^Rejected/ }).textContent).toBe("Rejected1");
    expect(screen.getByRole("tab", { name: /^All/ }).textContent).toBe("All4");
  });

  it("drops a decision from a tab it no longer belongs to", async () => {
    mocks.list.mockResolvedValue(proposedTab);
    mocks.update.mockResolvedValue({ ...lifetime, status: "approved" });
    render(<DecisionsView initial={owner} />);
    await userEvent.click(screen.getByRole("tab", { name: /^Proposed/ }));
    await openRow(lifetime.text);
    await userEvent.selectOptions(within(panelOf(lifetime.text)).getByLabelText("Status"), "approved");
    await waitFor(() => expect(screen.queryByText(lifetime.text)).toBeNull());
    expect(screen.getByText("No decisions match.")).toBeTruthy();
    expect(screen.getByRole("tab", { name: /^Proposed/ }).textContent).toBe("Proposed0");
  });

  it("sets a revisit day, clears it with No date, and the banner follows", async () => {
    mocks.update.mockResolvedValueOnce({ ...launch, revisit_on: "2026-11-08" }).mockResolvedValueOnce({ ...launch, revisit_on: null });
    render(<DecisionsView initial={owner} />);
    await openRow(launch.text);
    const panel = within(panelOf(launch.text));
    expect(panel.queryByRole("button", { name: "No date" })).toBeNull(); // nothing to clear yet
    const field = panel.getByLabelText("Revisit on");
    await userEvent.type(field, "2026-11-08");
    await waitFor(() => expect(mocks.update).toHaveBeenCalledWith(2, { revisit_on: "2026-11-08" }));
    expect(await screen.findByText("1 decision is due to be revisited this week.")).toBeTruthy();
    expect(row(launch.text).textContent).toContain("Revisit Sun 8 Nov 2026");
    await userEvent.click(await within(panelOf(launch.text)).findByRole("button", { name: "No date" }));
    expect(mocks.update).toHaveBeenLastCalledWith(2, { revisit_on: null });
    await waitFor(() => expect(screen.queryByText(/due to be revisited this week/)).toBeNull());
  });

  it("offers Replaced by when Replaced is chosen, and sends the chosen decision", async () => {
    mocks.list.mockResolvedValue(owner);
    mocks.update
      .mockResolvedValueOnce({ ...lifetime, status: "replaced" })
      .mockResolvedValueOnce({ ...lifetime, status: "replaced", replaced_by: { id: 2, text: launch.text } });
    render(<DecisionsView initial={owner} />);
    await openRow(lifetime.text);
    await userEvent.selectOptions(within(panelOf(lifetime.text)).getByLabelText("Status"), "replaced");
    await screen.findByText("Status changed to Replaced");
    const by = (await within(panelOf(lifetime.text)).findByLabelText("Replaced by")) as HTMLSelectElement;
    await waitFor(() => expect(within(by).getAllByRole("option").length).toBeGreaterThan(2));
    const options = within(by).getAllByRole("option").map((o) => o.textContent);
    expect(options[0]).toBe("No decision chosen");
    expect(options).not.toContain(lifetime.text); // not itself
    await userEvent.selectOptions(by, String(launch.id));
    expect(mocks.update).toHaveBeenLastCalledWith(3, { replaced_by_id: 2 });
    // the other decision learns what it replaces, without a reload
    await userEvent.click(wordsOf(launch.text));
    expect(within(panelOf(launch.text)).getByText(lifetime.text)).toBeTruthy();
  });

  it("says Could not update the decision. Try again. and leaves the row as it was", async () => {
    mocks.update.mockRejectedValue(new ApiError(500, "server_error", "x"));
    render(<DecisionsView initial={owner} />);
    await openRow(free.text);
    await userEvent.selectOptions(within(panelOf(free.text)).getByLabelText("Status"), "rejected");
    expect(await screen.findByText("Could not update the decision. Try again.")).toBeTruthy();
    expect(within(row(free.text)).getByText("Approved")).toBeTruthy();
    expect((within(panelOf(free.text)).getByLabelText("Status") as HTMLSelectElement).value).toBe("approved");
    expect(screen.queryByText(/Status changed to/)).toBeNull();
  });

  it("drops the row when the decision turns out to be gone (404)", async () => {
    mocks.update.mockRejectedValue(new ApiError(404, "not_found", "No such decision"));
    render(<DecisionsView initial={owner} />);
    await openRow(free.text);
    await userEvent.selectOptions(within(panelOf(free.text)).getByLabelText("Status"), "rejected");
    await waitFor(() => expect(screen.queryByText(free.text)).toBeNull());
  });
});

describe("a teammate reads, and does not change", () => {
  it("shows status and date as text, with no select and no date field", async () => {
    render(<DecisionsView initial={teammate} />);
    expect(screen.getAllByRole("tab")).toHaveLength(5);
    await openRow(free.text);
    const panel = within(panelOf(free.text));
    expect(panel.queryByLabelText("Status")).toBeNull();
    expect(panel.queryByLabelText("Revisit on")).toBeNull();
    expect(panel.getByText("Status")).toBeTruthy();
    expect(panel.getByText("Approved")).toBeTruthy();
    expect(panel.getByText("No date")).toBeTruthy();
    expect(panel.getByRole("link", { name: "Show in transcript" })).toBeTruthy();
  });
});

describe("links between decisions", () => {
  it("shows Replaced by: and Replaces: and opens the other decision by searching its words", async () => {
    mocks.list.mockResolvedValue({ ...replacedView, groups: [{ ...sharedMeeting(replacedView), decisions: [flat(replacedView).find((d) => d.id === 2)!] }] });
    render(<DecisionsView initial={replacedView} />);
    await openRow(lifetime.text);
    const panel = within(panelOf(lifetime.text));
    expect(panel.getByText("Replaced by:")).toBeTruthy();
    await userEvent.click(panel.getByRole("button", { name: launch.text }));
    expect(mocks.list).toHaveBeenCalledWith({ q: launch.text, status: undefined });
    await waitFor(() => expect(within(panelOf(launch.text)).getByText("Replaces:")).toBeTruthy());
    expect((screen.getByRole("searchbox", { name: "Search decisions" }) as HTMLInputElement).value).toBe(launch.text);
    expect(screen.getByRole("tab", { name: /^All/ }).getAttribute("aria-selected")).toBe("true");
  });
});

describe("related tasks open the task drawer", () => {
  it("reads the task and opens the drawer, and a change in the drawer shows on the row", async () => {
    const task: Task = { ...board.tasks[0], id: 1, title: "Update the pricing page", status: "todo" };
    mocks.taskGet.mockResolvedValue(task);
    mocks.taskList.mockResolvedValue(board);
    mocks.projects.mockResolvedValue({ projects: [] });
    mocks.milestones.mockResolvedValue({ milestones: [] });
    mocks.update.mockResolvedValue(undefined);
    render(<DecisionsView initial={owner} />);
    await openRow(free.text);
    await userEvent.click(within(panelOf(free.text)).getByRole("button", { name: /Update the pricing page/ }));
    expect(mocks.taskGet).toHaveBeenCalledWith(1);
    const drawer = await screen.findByRole("dialog", { name: "Task" });
    expect(within(drawer).getByLabelText("Task title")).toBeTruthy();
    await userEvent.click(within(drawer).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("says so when the task cannot be read", async () => {
    mocks.taskGet.mockRejectedValue(new ApiError(404, "not_found", "No such task"));
    render(<DecisionsView initial={owner} />);
    await openRow(free.text);
    await userEvent.click(within(panelOf(free.text)).getByRole("button", { name: /Update the pricing page/ }));
    expect(await screen.findByText("Could not open this task. Try again.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("the helpers", () => {
  it("knows the week the server counts for the banner (Monday to Sunday)", () => {
    expect(weekOf("2026-11-08")).toEqual(["2026-11-02", "2026-11-08"]); // a Sunday
    expect(weekOf("2026-11-09")).toEqual(["2026-11-09", "2026-11-15"]); // a Monday
    expect(isDueThisWeek({ status: "approved", revisit_on: "2026-11-08" }, "2026-11-08")).toBe(true);
    expect(isDueThisWeek({ status: "proposed", revisit_on: "2026-11-01" }, "2026-11-08")).toBe(false);
    expect(isDueThisWeek({ status: "replaced", revisit_on: "2026-11-08" }, "2026-11-08")).toBe(false);
    expect(isDueThisWeek({ status: "approved", revisit_on: null }, "2026-11-08")).toBe(false);
  });

  it("moves one decision between counts", () => {
    const counts = { all: 4, approved: 3, proposed: 1, replaced: 0, rejected: 0 };
    expect(movedCount(counts, "approved", "rejected")).toEqual({ all: 4, approved: 2, proposed: 1, replaced: 0, rejected: 1 });
    expect(movedCount(counts, "approved", "approved")).toBe(counts);
  });
});
