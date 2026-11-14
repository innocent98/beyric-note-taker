// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingView } from "@/components/app/meeting-view";
import { FORMER_MEMBER, MOVE_WARNING, teamActionMessage } from "@/components/app/meeting-team-card";
import { ApiError } from "@/lib/api/client";
import type { Meeting, TeamList, Template } from "@/lib/api/types";
import { teamsBody } from "./helpers";

// The meeting page's team card (lead-designed): share to a team, Move with its confirmation in his exact
// words, Move back, "Former member". Every meeting is a real body from capture (docs/fe-captures/teams/),
// the teams are Pat's (capture 46: personal space 2, Platform team 4, Client Y 5), the errors are the
// guide's (sections 4 to 6) with their exact strings.

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  share: vi.fn(),
  unshare: vi.fn(),
  move: vi.fn(),
  moveBack: vi.fn(),
  push: vi.fn(),
  refresh: vi.fn(),
  milestones: vi.fn(),
  seriesList: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push, refresh: mocks.refresh }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    milestones: { forMeeting: mocks.milestones, confirm: vi.fn() },
    series: { list: mocks.seriesList, create: vi.fn() },
    meetings: { get: mocks.get, share: mocks.share, unshare: mocks.unshare, move: mocks.move, moveBack: mocks.moveBack },
  },
}));

const templates: Template[] = [{ id: 1, name: "General", instructions: "g" }];
const teams = teamsBody<TeamList>("46-teams-list.json").teams;
const detail = (file: string) => teamsBody<Meeting>(file);
const show = (m: Meeting, withTeams = true) => render(<MeetingView initial={m} templates={templates} teams={withTeams ? teams : null} />);

const PRIVATE = "08-detail-private.json";
const SHARED = "13-share-named.json";
const MOVED = "25-move.json";
const MOVED_BY_ADMIN = "43-left-team-still-the-teams.json";
const MOVED_RECORDER_GONE = "44-recorder-deleted-account.json";

const error = (status: number, code: string, message: string) => new ApiError(status, code, message);

beforeEach(() => {
  cleanup();
  Object.values(mocks).forEach((fn) => fn.mockReset());
  mocks.milestones.mockResolvedValue({ milestones: [] });
  mocks.seriesList.mockResolvedValue({ timezone: "UTC", series: [] });
});

describe("a private meeting", () => {
  it("offers the teams but never the personal space, starting on the first", () => {
    show(detail(PRIVATE));
    const select = screen.getByRole("combobox", { name: "Team" }) as HTMLSelectElement;
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Platform team", "Client Y"]);
    expect(select.value).toBe("4");
    expect(screen.getByRole("heading", { level: 2, name: "Share with a team" })).toBeTruthy();
    expect(screen.queryByRole("switch", { name: "Share with team" })).toBeNull(); // the old switch is gone
  });

  it("Share sends the chosen team and shows the meeting as shared", async () => {
    mocks.share.mockResolvedValue(detail(SHARED));
    show(detail(PRIVATE));
    fireEvent.change(screen.getByRole("combobox", { name: "Team" }), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Share" }));
    await waitFor(() => expect(mocks.share).toHaveBeenCalledWith(1, 5));
    expect(await screen.findByRole("heading", { level: 2, name: "Shared with Platform team" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Stop sharing" })).toBeTruthy();
  });

  it("a shared meeting can stop sharing, or move the share to another team", async () => {
    mocks.unshare.mockResolvedValue(detail(PRIVATE));
    mocks.share.mockResolvedValue({ ...detail(SHARED), team: { id: 5, name: "Client Y" } });
    show(detail(SHARED));
    expect(screen.queryByRole("button", { name: /instead/ })).toBeNull(); // same team chosen: nothing to switch
    fireEvent.change(screen.getByRole("combobox", { name: "Team" }), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Share with Client Y instead" }));
    await waitFor(() => expect(mocks.share).toHaveBeenCalledWith(1, 5));
    fireEvent.click(await screen.findByRole("button", { name: "Stop sharing" }));
    await waitFor(() => expect(mocks.unshare).toHaveBeenCalledWith(1));
    // The guide asks for this to be said (section 7): tasks go back, assignments are cleared.
    expect(await screen.findByText(/Share with a team/)).toBeTruthy();
  });

  it("says what stopping does to the tasks", () => {
    show(detail(SHARED));
    expect(document.body.textContent).toContain("Tasks from this meeting go back to you; teammates' assignments are removed.");
  });
});

describe("Move", () => {
  it("asks first, in his exact words, and does nothing on Cancel", () => {
    show(detail(PRIVATE));
    fireEvent.click(screen.getByRole("button", { name: "Move to team" }));
    expect(MOVE_WARNING).toBe("This meeting will belong to the team. It stays with the team even if you leave or delete your account.");
    const dialog = screen.getByRole("dialog", { name: "Move this meeting to Platform team?" });
    expect(within(dialog).getByText(MOVE_WARNING)).toBeTruthy();
    expect(dialog.textContent).toContain("Posts already made to Slack stay where they are.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(mocks.move).not.toHaveBeenCalled();
  });

  it("moves the meeting to the chosen team after the confirmation, and the page becomes the team's", async () => {
    mocks.move.mockResolvedValue(detail(MOVED));
    show(detail(PRIVATE));
    fireEvent.change(screen.getByRole("combobox", { name: "Team" }), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Move to team" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Move meeting" }));
    await waitFor(() => expect(mocks.move).toHaveBeenCalledWith(1, 4));
    expect(await screen.findByRole("heading", { level: 2, name: "Belongs to Platform team" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Share" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Move to team" })).toBeNull();
    expect(document.querySelector(".details")?.textContent).toContain("TeamPlatform team");
  });

  it("is not offered when the server says the meeting cannot be moved (not finished, or already moved)", () => {
    show({ ...detail(PRIVATE), can_move: false });
    expect(screen.queryByRole("button", { name: "Move to team" })).toBeNull();
  });

  it.each([
    [409, "meeting_not_ready", "Wait until the notes are ready, then move the meeting."],
    [409, "already_moved", "This meeting already belongs to another team. An owner or admin of that team can move it back first."],
    [409, "personal_team", "Choose a team. Your personal space is only yours; nobody else would see the meeting."],
    [409, "multi_team_disabled", "Moving a meeting to a team is not available yet."],
  ])("shows %i %s in the guide's own words", async (status, code, message) => {
    mocks.move.mockRejectedValue(error(status, code, message));
    mocks.get.mockResolvedValue(detail(PRIVATE));
    show(detail(PRIVATE));
    fireEvent.click(screen.getByRole("button", { name: "Move to team" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Move meeting" }));
    expect((await screen.findByRole("alert")).textContent).toContain(message);
    await waitFor(() => expect(mocks.get).toHaveBeenCalled()); // a 409 means the state moved: read it again
  });

  it("a team the person is no longer in (404 'No such team.') is said plainly and the page asks for fresh teams", async () => {
    mocks.move.mockRejectedValue(error(404, "not_found", "No such team."));
    show(detail(PRIVATE));
    fireEvent.click(screen.getByRole("button", { name: "Move to team" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Move meeting" }));
    expect((await screen.findByRole("alert")).textContent).toContain("You are no longer in that team. Choose another.");
    expect(mocks.refresh).toHaveBeenCalled();
  });

  it("a share that needs a team (422 team_required) shows its message", async () => {
    const message = "You are on more than one team. Choose which team to share this meeting with.";
    mocks.share.mockRejectedValue(error(422, "team_required", message));
    show(detail(PRIVATE));
    fireEvent.click(screen.getByRole("button", { name: "Share" }));
    expect((await screen.findByRole("alert")).textContent).toContain(message);
  });

  it("teamActionMessage falls back to the plain default for anything else", () => {
    expect(teamActionMessage(error(404, "not_found", "No such meeting"))).toBe("Something went wrong. Please try again.");
    expect(teamActionMessage(new Error("x"))).toBe("Something went wrong. Please try again.");
  });
});

describe("a meeting the team owns", () => {
  it("says whose it is and who recorded it, with Details saying Recorded by", () => {
    show(detail(MOVED_BY_ADMIN));
    expect(screen.getByRole("heading", { level: 2, name: "Belongs to Platform team" })).toBeTruthy();
    expect(document.body.textContent).toContain("Recorded by Ada Example.");
    expect(document.querySelector(".details")?.textContent).toContain("Recorded byAda Example");
    expect(document.querySelector(".details")?.textContent).not.toContain("Shared by");
  });

  it("an owner or admin can move it back to the recorder, after a confirmation that says what happens to the tasks", async () => {
    mocks.moveBack.mockResolvedValue(detail(PRIVATE));
    show(detail(MOVED_BY_ADMIN));
    fireEvent.click(screen.getByRole("button", { name: "Move back to Ada Example" }));
    const dialog = screen.getByRole("dialog", { name: "Move this meeting back to Ada Example?" });
    expect(dialog.textContent).toContain("Tasks from this meeting go back to Ada Example; teammates' assignments are removed.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Move back" }));
    await waitFor(() => expect(mocks.moveBack).toHaveBeenCalledWith(1));
    expect(await screen.findByRole("heading", { level: 2, name: "Share with a team" })).toBeTruthy();
  });

  it("the recorder who is also an owner sees 'Move back to you'", () => {
    show(detail(MOVED));
    expect(screen.getByRole("button", { name: "Move back to you" })).toBeTruthy();
    expect(document.body.textContent).toContain("Recorded by you.");
  });

  it("whose recorder's account is gone shows 'Former member' and no way to give it back", () => {
    expect(detail(MOVED_RECORDER_GONE).shared_by).toBe(FORMER_MEMBER);
    show(detail(MOVED_RECORDER_GONE));
    expect(document.body.textContent).toContain("Recorded by Former member.");
    expect(document.body.textContent).toContain("That account is gone, so it cannot be given back.");
    expect(screen.queryByRole("button", { name: /Move back/ })).toBeNull();
    expect(document.querySelector(".details")?.textContent).toContain("Recorded byFormer member");
  });

  it("shows no Move back to someone who may not (the server's can_move_back)", () => {
    show({ ...detail(MOVED_BY_ADMIN), can_move_back: false });
    expect(screen.queryByRole("button", { name: /Move back/ })).toBeNull();
  });

  it("a refused move back (403 team_forbidden) is shown in the guide's words", async () => {
    const message = "Only an owner or admin of the team can move it back.";
    mocks.moveBack.mockRejectedValue(error(403, "team_forbidden", message));
    show(detail(MOVED_BY_ADMIN));
    fireEvent.click(screen.getByRole("button", { name: "Move back to Ada Example" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Move back" }));
    expect((await screen.findByRole("alert")).textContent).toContain(message);
  });
});

describe("the meeting's own team answers for its series and milestones", () => {
  it("asks for the milestones of the team it is shared with, or the default team for a private one", async () => {
    show(detail(SHARED));
    await waitFor(() => expect(mocks.milestones).toHaveBeenCalledWith(1, 4));
    cleanup();
    mocks.milestones.mockClear();
    show(detail(PRIVATE));
    await waitFor(() => expect(mocks.milestones).toHaveBeenCalledWith(1, null));
  });
});

describe("when the switch is on but the teams could not be read", () => {
  it("shows a plain error with Try again, not the old share switch (which would send no team)", () => {
    render(<MeetingView initial={detail(PRIVATE)} templates={templates} teams={null} teamsFailed teamSize={0} />);
    expect(screen.queryByRole("switch", { name: "Share with team" })).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("Could not load your teams, so sharing is not available right now.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(mocks.refresh).toHaveBeenCalled();
  });
  it("draws nothing of it for someone who may not share", () => {
    render(<MeetingView initial={{ ...detail(PRIVATE), can_share: false }} templates={templates} teams={null} teamsFailed />);
    expect(screen.queryByText(/Could not load your teams/)).toBeNull();
  });
});

describe("with the switch off", () => {
  const withSize = (m: Meeting, size = 3) => render(<MeetingView initial={m} templates={templates} teamSize={size} />);

  it("keeps the old share switch and none of the team parts", () => {
    withSize(detail(PRIVATE));
    expect(screen.getByRole("switch", { name: "Share with team" })).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Team" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Move to team" })).toBeNull();
    expect(document.querySelector(".details")?.textContent).not.toContain("Team");
  });

  it("asks for the milestones with no team of its own, as before", async () => {
    withSize(detail(SHARED));
    await waitFor(() => expect(mocks.milestones).toHaveBeenCalledWith(1, undefined));
  });

  it("calls share with no team in the body", async () => {
    mocks.share.mockResolvedValue(detail(SHARED));
    withSize(detail(PRIVATE));
    fireEvent.click(screen.getByRole("switch", { name: "Share with team" }));
    await waitFor(() => expect(mocks.share).toHaveBeenCalledWith(1));
  });

  it("shows no Recorded by label for a meeting the person did not record, only Shared by", () => {
    withSize({ ...detail(SHARED), shared_by: "Dan Okoro", can_share: false });
    expect(document.querySelector(".details")?.textContent).toContain("Shared byDan Okoro");
  });
});
