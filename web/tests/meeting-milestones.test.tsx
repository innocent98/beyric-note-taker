// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingView } from "@/components/app/meeting-view";
import { ApiError } from "@/lib/api/client";
import type { MeetingMilestones, Meeting, Milestone, Template } from "@/lib/api/types";
import { captured, projectsBody } from "./helpers";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

// The meeting page's milestone line and the "Set milestone" offer (LEAD-DESIGNED, not in the
// designer's file), fed the bodies captured from the running API (docs/fe-captures/projects-w4/):
// capture 38 is the proposal as the worker stored it, 44 the same milestone after Confirm.

const mocks = vi.hoisted(() => ({ forMeeting: vi.fn(), confirm: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({
  api: { milestones: mocks, meetings: { get: vi.fn() } },
}));

const templates: Template[] = [{ id: 1, name: "General", instructions: "g" }];
const proposal = projectsBody<MeetingMilestones>("38-meeting-milestones.json");
const confirmed = projectsBody<Milestone>("44-confirm.json");
const meeting = captured<Meeting>("47-meeting-detail-ready.json");
const show = () => render(<MeetingView initial={{ ...meeting, status: "ready" }} templates={templates} />);

beforeEach(() => Object.values(mocks).forEach((fn) => fn.mockReset()));
afterEach(cleanup);

describe("the meeting page's milestone", () => {
  it("offers 'Set milestone: <name>, <date>?' with the words said and a Confirm button", async () => {
    mocks.forMeeting.mockResolvedValue(proposal);
    show();
    expect(await screen.findByText("Set milestone: Client Portal launch, 8 Dec?")).toBeTruthy();
    expect(screen.getByText(/Launch is December 8\./)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeTruthy();
    expect(screen.queryByText("Milestone")).toBeNull(); // not a milestone yet
  });

  it("confirms, then shows the Milestone row in Details and no offer", async () => {
    mocks.forMeeting.mockResolvedValue(proposal);
    mocks.confirm.mockResolvedValue(confirmed);
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    expect(mocks.confirm).toHaveBeenCalledWith(confirmed.id);
    await waitFor(() => expect(screen.queryByText(/Set milestone:/)).toBeNull());
    const row = screen.getByText("Milestone");
    expect(row.nextElementSibling?.textContent).toBe("Client Portal launch, 8 Dec");
  });

  it("draws no button for someone who may not confirm (a member): the line is only the Details row", async () => {
    mocks.forMeeting.mockResolvedValue({ milestones: proposal.milestones.map((m) => ({ ...m, can_confirm: false })) });
    show();
    await waitFor(() => expect(mocks.forMeeting).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
    expect(screen.queryByText(/Set milestone:/)).toBeNull();
  });

  it("shows the refusal when Confirm fails, and keeps the offer", async () => {
    mocks.forMeeting.mockResolvedValue(proposal);
    mocks.confirm.mockRejectedValue(new ApiError(403, "team_forbidden", "Only an owner or admin of the team can do that."));
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Only an owner or admin of the team can do that.")).toBeTruthy();
    expect(screen.getByText("Set milestone: Client Portal launch, 8 Dec?")).toBeTruthy();
  });

  it("draws nothing when the read fails, and nothing for a meeting that is not ready", async () => {
    mocks.forMeeting.mockRejectedValue(new ApiError(500, "gateway_error", "x"));
    const { unmount } = show();
    await waitFor(() => expect(mocks.forMeeting).toHaveBeenCalled());
    expect(screen.queryByText(/Set milestone:/)).toBeNull();
    unmount();
    mocks.forMeeting.mockClear();
    render(<MeetingView initial={{ ...meeting, status: "processing", notes: null, notes_html: null }} templates={templates} />);
    expect(mocks.forMeeting).not.toHaveBeenCalled();
    expect(within(document.body).queryByText(/Set milestone:/)).toBeNull();
  });
});
