// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DecisionsView } from "@/components/app/decisions-view";
import { ApiError, NETWORK_MESSAGE } from "@/lib/api/client";
import type { DecisionList } from "@/lib/api/types";
import { workspaceBody, workspaceError } from "./helpers";

// The Decisions screen is fed the bodies captured from the running API
// (docs/fe-captures/ai-workspace-w1/, cited in docs/fe-integration-guide-ai-workspace.md section 3).

const mocks = vi.hoisted(() => ({ list: vi.fn(), hide: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { decisions: { list: mocks.list, hide: mocks.hide } } }));

const all = workspaceBody<DecisionList>("13-decisions.json");
const byWords = workspaceBody<DecisionList>("14-decisions-search-text.json");
const byCode = workspaceBody<DecisionList>("15-decisions-search-title.json");
const none = workspaceBody<DecisionList>("16-decisions-search-none.json");
const page1 = workspaceBody<DecisionList>("17-decisions-page-1.json");
const page2 = workspaceBody<DecisionList>("18-decisions-page-2.json");

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
});
afterEach(cleanup);

const search = (text: string) => userEvent.type(screen.getByRole("searchbox", { name: "Search decisions" }), text);

describe("decisions", () => {
  it("groups the decisions by meeting, newest first, each with the day, the title and Open notes", () => {
    render(<DecisionsView initial={all} />);
    expect(screen.getByRole("heading", { level: 1, name: "Decisions" })).toBeTruthy();
    const titles = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(titles).toEqual(["Google Meet vag-cccc-ddd", "Zoom 81234567890", "Google Meet nat-aaaa-bbb"]);
    expect(screen.getByText("Keep the data export in CSV for version one; Excel can wait.")).toBeTruthy();
    expect(screen.getByText("Mon 2 Nov 2026")).toBeTruthy();
    const links = screen.getAllByRole("link", { name: "Open notes" }).map((a) => a.getAttribute("href"));
    expect(links).toEqual(["/meetings/3", "/meetings/2", "/meetings/1"]);
    expect(screen.queryByRole("button", { name: "Show older meetings" })).toBeNull();
  });

  it("says there are no decisions yet when nothing has been read from any notes", () => {
    render(<DecisionsView initial={{ groups: [], has_more: false, next_before_id: null }} />);
    expect(screen.getByText("No decisions yet.")).toBeTruthy();
    expect(screen.getByText(/usually a minute after the notes are written/)).toBeTruthy();
  });

  it("searches the API after typing pauses, and shows only the matching decision", async () => {
    mocks.list.mockResolvedValue(byWords);
    render(<DecisionsView initial={all} />);
    await search("metrika");
    await waitFor(() => expect(mocks.list).toHaveBeenCalledWith({ q: "metrika" }));
    expect(mocks.list).toHaveBeenCalledTimes(1); // one call for the whole word, not one per letter
    expect(await screen.findByText("Proceed with the Metrika rollout in the next release.")).toBeTruthy();
    expect(screen.queryByText("Renew the vendor for one more year.")).toBeNull();
  });

  it("lists every decision of a meeting when its code matches", async () => {
    mocks.list.mockResolvedValue(byCode);
    render(<DecisionsView initial={all} />);
    await search("81234567890");
    expect(await screen.findByText("Confirm the claimed discount in writing before relying on it.")).toBeTruthy();
    expect(screen.getByText("Renew the vendor for one more year.")).toBeTruthy();
  });

  it("says nothing matches the search (the captured empty answer)", async () => {
    mocks.list.mockResolvedValue(none);
    render(<DecisionsView initial={all} />);
    await search("zebra");
    expect(await screen.findByText("No decisions match your search.")).toBeTruthy();
    expect(screen.queryByText("No decisions yet.")).toBeNull();
  });

  it("shows Searching... while the answer is on its way, and keeps the old list under it", async () => {
    let answer: (list: DecisionList) => void = () => undefined;
    mocks.list.mockReturnValue(new Promise<DecisionList>((resolve) => (answer = resolve)));
    render(<DecisionsView initial={all} />);
    await search("x");
    expect(await screen.findByText("Searching...")).toBeTruthy();
    expect(screen.getByText("Renew the vendor for one more year.")).toBeTruthy();
    answer(none);
    await waitFor(() => expect(screen.queryByText("Searching...")).toBeNull());
  });

  it("drops a slow answer to an older search", async () => {
    let first: (list: DecisionList) => void = () => undefined;
    mocks.list.mockReturnValueOnce(new Promise<DecisionList>((resolve) => (first = resolve)));
    mocks.list.mockResolvedValueOnce(byWords);
    render(<DecisionsView initial={all} />);
    await search("a");
    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(1));
    await search("b");
    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Proceed with the Metrika rollout in the next release.")).toBeTruthy();
    first(none); // the older search answers last and must not replace the newer list
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByText("No decisions match your search.")).toBeNull();
    expect(screen.getByText("Proceed with the Metrika rollout in the next release.")).toBeTruthy();
  });

  it("says the search failed and tries again on request", async () => {
    mocks.list.mockRejectedValueOnce(new ApiError(0, "network_error", "x")).mockResolvedValueOnce(byWords);
    render(<DecisionsView initial={all} />);
    await search("metrika");
    expect((await screen.findByRole("alert")).textContent).toContain("Could not load the decisions.");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Proceed with the Metrika rollout in the next release.")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(mocks.list).toHaveBeenLastCalledWith({ q: "metrika" });
  });

  it("loads older meetings with before_id and adds them under the first page", async () => {
    mocks.list.mockResolvedValue(page2);
    render(<DecisionsView initial={page1} />);
    expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: "Show older meetings" }));
    expect(mocks.list).toHaveBeenCalledWith({ q: undefined, before_id: 3 });
    await waitFor(() => expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(2));
    expect(screen.getByRole("button", { name: "Show older meetings" })).toBeTruthy(); // page 2 says has_more
  });

  it("says when older meetings could not be loaded", async () => {
    mocks.list.mockRejectedValue(new ApiError(0, "network_error", "x"));
    render(<DecisionsView initial={page1} />);
    await userEvent.click(screen.getByRole("button", { name: "Show older meetings" }));
    expect(await screen.findByText("Could not load older meetings. Try again.")).toBeTruthy();
  });
});

describe("remove from log", () => {
  const first = all.groups[0].decisions[0];

  it("asks first, then hides the decision and keeps the rest of its meeting", async () => {
    mocks.hide.mockResolvedValue(undefined);
    render(<DecisionsView initial={all} />);
    await userEvent.click(screen.getByRole("button", { name: `Remove from log: ${first.text}` }));
    expect(mocks.hide).not.toHaveBeenCalled();
    const ask = screen.getByRole("group", { name: "Remove this decision from the log?" });
    expect(ask.textContent).toContain("The notes keep it.");
    await userEvent.click(within(ask).getByRole("button", { name: "Remove" }));
    expect(mocks.hide).toHaveBeenCalledWith(first.id);
    await waitFor(() => expect(screen.queryByText(first.text)).toBeNull());
    expect(screen.getByText("Leave the work estimate unassigned for now.")).toBeTruthy();
  });

  it("drops a meeting from the list when its last decision is removed", async () => {
    mocks.hide.mockResolvedValue(undefined);
    render(<DecisionsView initial={all} />);
    const only = all.groups[2].decisions[0];
    await userEvent.click(screen.getByRole("button", { name: `Remove from log: ${only.text}` }));
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(screen.queryByRole("heading", { level: 2, name: "Google Meet nat-aaaa-bbb" })).toBeNull());
  });

  it("changes nothing when the owner keeps the decision", async () => {
    render(<DecisionsView initial={all} />);
    await userEvent.click(screen.getByRole("button", { name: `Remove from log: ${first.text}` }));
    await userEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(mocks.hide).not.toHaveBeenCalled();
    expect(screen.getByText(first.text)).toBeTruthy();
    expect(screen.queryByRole("group")).toBeNull();
  });

  it("treats the captured 404 as removed: it is gone either way", async () => {
    const { status, code, message } = workspaceError("24-decision-hide-foreign.json");
    mocks.hide.mockRejectedValue(new ApiError(status, code, message));
    render(<DecisionsView initial={all} />);
    await userEvent.click(screen.getByRole("button", { name: `Remove from log: ${first.text}` }));
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(screen.queryByText(first.text)).toBeNull());
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps the decision and says what went wrong on any other failure", async () => {
    mocks.hide.mockRejectedValue(new ApiError(0, "network_error", NETWORK_MESSAGE));
    render(<DecisionsView initial={all} />);
    await userEvent.click(screen.getByRole("button", { name: `Remove from log: ${first.text}` }));
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect((await screen.findByRole("alert")).textContent).toBe(NETWORK_MESSAGE);
    expect(screen.getByText(first.text)).toBeTruthy();
  });
});
