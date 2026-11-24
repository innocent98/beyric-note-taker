// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TeamSwitcher } from "@/components/app/team-switcher";
import { TeamView } from "@/components/app/team-view";
import { TEAM_STORAGE_KEY } from "@/lib/active-team";
import { ApiError } from "@/lib/api/client";
import type { Team, TeamList, TeamMembers } from "@/lib/api/types";
import { teamBody, teamsBody, workspacesBody } from "./helpers";

// Deleting a workspace and a class's words (workspaces W4, lead-designed). Fed the bodies captured from the
// running API (docs/fe-captures/workspaces/, `scripts/capture_workspaces_lifecycle.py`): the class Ada made
// (11), the refusal when someone is still in the team (21), and her teams at the limit (13).

const mocks = vi.hoisted(() => ({
  remove: vi.fn(),
  get: vi.fn(),
  members: vi.fn(),
  removeMember: vi.fn(),
}));
vi.mock("@/lib/api/browser", () => ({ api: { teams: { remove: mocks.remove }, team: mocks } }));
const nav = vi.hoisted(() => ({ push: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, refresh: nav.refresh }),
  usePathname: () => "/team",
  useSearchParams: () => new URLSearchParams(),
}));

const klass = workspacesBody<Team>("11-ada-class-team.json");
const refused = workspacesBody<{ error: { code: string; message: string } }>("21-ada-delete-with-members.json").error;
const atLimit = workspacesBody<TeamList>("13-ada-teams-at-limit.json");
const platform = teamsBody<Team>("54-team-header-x.json"); // an ordinary team its owner is in
const afterAda = teamBody<TeamMembers>("25-members-after-ada.json");
const solo: TeamMembers = { members: afterAda.members.slice(0, 1), invites: [] };
const crowd: TeamMembers = { members: afterAda.members, invites: [] };

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
  nav.push.mockReset();
  nav.refresh.mockReset();
  window.sessionStorage.clear();
});
afterEach(cleanup);

describe("Delete team", () => {
  const owner: Team = { ...platform, my_role: "owner", can_manage: true, can_change_roles: true, is_personal: false };

  it("while other people are in the team the button is off and says why, in the API's words", () => {
    render(<TeamView initialTeam={owner} initialMembers={crowd} multiTeam />);
    expect((screen.getByRole("button", { name: "Delete team" }) as HTMLButtonElement).disabled).toBe(true);
    // The same sentence the API refuses with (capture 21), so the screen and the refusal agree.
    expect(refused.message).toBe(
      "Other people are still in this team. Remove them first: you can delete it once you are its only member.",
    );
    expect(screen.getByText(refused.message)).toBeTruthy();
  });

  it("alone, it asks first and says where the meetings go, and Cancel changes nothing", async () => {
    render(<TeamView initialTeam={owner} initialMembers={solo} multiTeam />);
    await userEvent.click(screen.getByRole("button", { name: "Delete team" }));
    const ask = screen.getByRole("group", { name: `Delete ${owner.name}?` });
    expect(ask.textContent).toContain("each goes back to the person who recorded it, private, in their personal space");
    await userEvent.click(within(ask).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: `Delete ${owner.name}?` })).toBeNull();
    expect(mocks.remove).not.toHaveBeenCalled();
  });

  it("confirming deletes it, forgets it for this tab and goes to the Teams list", async () => {
    mocks.remove.mockResolvedValue(undefined);
    window.sessionStorage.setItem(TEAM_STORAGE_KEY, `1:${owner.id}`);
    render(<TeamView initialTeam={owner} initialMembers={solo} multiTeam />);
    await userEvent.click(screen.getByRole("button", { name: "Delete team" }));
    const ask = screen.getByRole("group", { name: `Delete ${owner.name}?` });
    await userEvent.click(within(ask).getByRole("button", { name: "Delete team" }));
    await waitFor(() => expect(mocks.remove).toHaveBeenCalledWith(owner.id));
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBeNull();
    expect(nav.push).toHaveBeenCalledWith("/teams");
    expect(nav.refresh).toHaveBeenCalled(); // the switcher was drawn by the server
  });

  it("a refusal (someone joined meanwhile) is shown as the API wrote it and the screen stays", async () => {
    mocks.remove.mockRejectedValue(new ApiError(409, refused.code, refused.message));
    render(<TeamView initialTeam={owner} initialMembers={solo} multiTeam />);
    await userEvent.click(screen.getByRole("button", { name: "Delete team" }));
    await userEvent.click(within(screen.getByRole("group", { name: `Delete ${owner.name}?` })).getByRole("button", { name: "Delete team" }));
    expect((await screen.findByRole("alert")).textContent).toContain(refused.message);
    expect(nav.push).not.toHaveBeenCalled();
  });

  it("only the owner of an ordinary team is offered it", () => {
    const { unmount } = render(<TeamView initialTeam={{ ...owner, my_role: "admin" }} initialMembers={solo} multiTeam />);
    expect(screen.queryByRole("button", { name: "Delete team" })).toBeNull();
    unmount();
    const personal = teamsBody<Team>("53-team-no-header.json");
    render(<TeamView initialTeam={{ ...personal, my_role: "owner" }} initialMembers={solo} multiTeam />);
    expect(screen.queryByRole("button", { name: "Delete team" })).toBeNull();
  });
});

describe("a class", () => {
  it("is drawn in a school's words: Class, Students, Lecturer", () => {
    expect(klass.kind).toBe("class");
    render(<TeamView initialTeam={klass} initialMembers={solo} multiTeam />);
    expect(screen.getByRole("heading", { level: 1, name: "Class" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Students" })).toBeTruthy();
    expect(screen.getByText("1 student, 0 pending")).toBeTruthy();
    expect(screen.getByText("Lecturer")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete class" })).toBeTruthy();
  });

  it("the switcher tags it, and an ordinary team is not tagged", () => {
    render(<TeamSwitcher teams={atLimit.teams} variant="sidebar" />);
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toContain("Maths 101 (class)");
    expect(options).toContain("Reading group");
    expect(options).toContain("My team (personal)");
  });
});
