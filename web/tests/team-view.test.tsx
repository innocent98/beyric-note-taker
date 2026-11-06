// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TeamView } from "@/components/app/team-view";
import { ApiError } from "@/lib/api/client";
import type { Team, TeamInviteResult, TeamInvitesCreated, TeamMembers } from "@/lib/api/types";
import { teamBody } from "./helpers";

// Fed the bodies captured from the running API (docs/fe-captures/team-w3/, cited in
// docs/fe-integration-guide-team.md): Dan's view of a team of three with Ada's invitation pending
// (capture 29-team-three, 12-members-with-invites) and a member's view (33).
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  update: vi.fn(),
  members: vi.fn(),
  updateMember: vi.fn(),
  removeMember: vi.fn(),
  invite: vi.fn(),
  resend: vi.fn(),
  revoke: vi.fn(),
}));
vi.mock("@/lib/api/browser", () => ({ api: { team: mocks } }));

const team = teamBody<Team>("29-team-three.json");
const withInvites = teamBody<TeamMembers>("12-members-with-invites.json");
const afterAda = teamBody<TeamMembers>("25-members-after-ada.json");
// Dan's list after Ada joined, with one open invitation added from capture 12.
const list: TeamMembers = { members: afterAda.members, invites: withInvites.invites.slice(0, 1) };
const memberView = teamBody<TeamMembers>("33-member-members.json");
const asMember: Team = { ...team, my_role: "member", can_manage: false, can_change_roles: false };

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
  window.sessionStorage.clear();
});
afterEach(cleanup);

describe("the Team screen", () => {
  it("draws the title, Invite people, the members, the count line and the two info cards", () => {
    render(<TeamView initialTeam={team} initialMembers={list} />);
    expect(screen.getByRole("heading", { level: 1, name: "Team" })).toBeTruthy();
    expect(screen.getByText("People on your team can be assigned tasks from meetings.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Invite people" })).toBeTruthy();
    expect(screen.getByText(`${list.members.length} members, 1 pending`)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "How tasks are assigned" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Roles" })).toBeTruthy();
    expect(screen.getByText("You")).toBeTruthy();
    expect(screen.getByText(/manages settings and the team/)).toBeTruthy(); // the billing words are trimmed
    expect(screen.queryByText(/billing/)).toBeNull();
  });

  it("shows 'Matched in meetings as' with the stored names, and 'N open'", () => {
    render(<TeamView initialTeam={team} initialMembers={list} />);
    const ada = list.members.find((m) => m.aliases.length > 0)!;
    expect(screen.getByText(`Matched in meetings as ${ada.aliases.join(", ")}`)).toBeTruthy();
    expect(screen.getAllByText("0 open").length).toBe(list.members.length);
  });

  it("draws an invited person as a row with the Invited pill, Pending, Resend and cancel", async () => {
    mocks.resend.mockResolvedValue(list.invites[0]);
    mocks.get.mockResolvedValue(team);
    mocks.members.mockResolvedValue(list);
    render(<TeamView initialTeam={team} initialMembers={list} />);
    const invite = list.invites[0];
    expect(screen.getAllByText(invite.email).length).toBeGreaterThan(0); // the name is the address: no name given
    expect(screen.getByText("Invited")).toBeTruthy();
    expect(screen.getByText("Pending")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Resend" }));
    expect(mocks.resend).toHaveBeenCalledWith(invite.id);
    expect(await screen.findByText(`Invite sent again to ${invite.email}`)).toBeTruthy();
    mocks.revoke.mockResolvedValue(undefined);
    await userEvent.click(screen.getByRole("button", { name: `Cancel invitation to ${invite.email}` }));
    expect(mocks.revoke).toHaveBeenCalledWith(invite.id);
    await waitFor(() => expect(screen.queryByText("Invited")).toBeNull());
  });

  it("shows an expired invitation as Expired", () => {
    render(<TeamView initialTeam={team} initialMembers={{ ...list, invites: [{ ...list.invites[0], status: "expired" }] }} />);
    expect(screen.getByText("Expired")).toBeTruthy();
    expect(screen.queryByText("Invited")).toBeNull();
  });

  it("changes a role from the select, only where the API says the caller may", async () => {
    mocks.updateMember.mockResolvedValue(list);
    render(<TeamView initialTeam={team} initialMembers={list} />);
    const changeable = list.members.filter((m) => m.can_change_role);
    expect(screen.getAllByRole("combobox").length).toBe(changeable.length);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: `Role of ${changeable[0].name}` }), "admin");
    expect(mocks.updateMember).toHaveBeenCalledWith(changeable[0].user_id, { role: "admin" });
    expect(screen.getAllByText("Owner").length).toBeGreaterThan(0); // the owner's role is text, not a select
  });

  it("asks before removing someone, says what happens, and removes on confirm", async () => {
    mocks.removeMember.mockResolvedValue(undefined);
    mocks.get.mockResolvedValue(team);
    mocks.members.mockResolvedValue({ ...list, members: list.members.slice(0, 1) });
    render(<TeamView initialTeam={team} initialMembers={list} />);
    const victim = list.members.find((m) => m.can_remove && !m.is_you)!;
    await userEvent.click(screen.getByRole("button", { name: `Remove ${victim.name}` }));
    const ask = screen.getByRole("group", { name: `Remove ${victim.name}?` });
    expect(ask.textContent).toContain("Open tasks assigned to them become unassigned");
    expect(mocks.removeMember).not.toHaveBeenCalled();
    await userEvent.click(within(ask).getByRole("button", { name: "Remove" }));
    expect(mocks.removeMember).toHaveBeenCalledWith(victim.user_id);
    expect(await screen.findByText(`${victim.name} was removed`)).toBeTruthy();
  });

  it("edits the names a person is matched by (lead-designed) and shows the API's cleaned list", async () => {
    const me = list.members.find((m) => m.is_you)!;
    mocks.updateMember.mockResolvedValue({
      ...list,
      members: list.members.map((m) => (m.user_id === me.user_id ? { ...m, aliases: ["Dan", "Danny"] } : m)),
    });
    render(<TeamView initialTeam={team} initialMembers={list} />);
    await userEvent.click(screen.getAllByRole("button", { name: /names/i })[0]);
    const field = screen.getByLabelText(/Names .* is called in meetings/);
    await userEvent.clear(field);
    await userEvent.type(field, "Dan, Danny, ");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mocks.updateMember).toHaveBeenCalledWith(list.members[0].user_id, { aliases: ["Dan", "Danny"] });
    expect(await screen.findByText("Matched in meetings as Dan, Danny")).toBeTruthy();
  });

  it("shows a member no invite button, no role selects and no switch, and lets them leave", async () => {
    mocks.removeMember.mockResolvedValue(undefined);
    mocks.get.mockResolvedValue(asMember);
    mocks.members.mockResolvedValue(memberView);
    render(<TeamView initialTeam={asMember} initialMembers={memberView} />);
    expect(screen.queryByRole("button", { name: "Invite people" })).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("switch")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Leave team" }));
    await userEvent.click(within(screen.getByRole("group", { name: "Leave this team?" })).getByRole("button", { name: "Leave team" }));
    expect(mocks.removeMember).toHaveBeenCalledWith(memberView.members.find((m) => m.is_you)!.user_id);
  });

  it("has the Share new meetings switch (lead-designed) for owners and admins, off by default", async () => {
    mocks.update.mockResolvedValue({ ...team, share_new_meetings: true });
    render(<TeamView initialTeam={team} initialMembers={list} />);
    const toggle = screen.getByRole("switch", { name: "Share new meetings with the team" });
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    await userEvent.click(toggle);
    expect(mocks.update).toHaveBeenCalledWith({ share_new_meetings: true });
    await waitFor(() => expect(toggle.getAttribute("aria-checked")).toBe("true"));
  });

  it("says what the API said when a change is refused, and keeps the screen", async () => {
    mocks.updateMember.mockRejectedValue(new ApiError(403, "team_forbidden", "Only the owner of the team can change roles."));
    render(<TeamView initialTeam={team} initialMembers={list} />);
    const changeable = list.members.find((m) => m.can_change_role)!;
    await userEvent.selectOptions(screen.getByRole("combobox", { name: `Role of ${changeable.name}` }), "admin");
    expect(await screen.findByText("Only the owner of the team can change roles.")).toBeTruthy();
  });

  it("says it could not load and Try again asks the API once more", async () => {
    mocks.get.mockResolvedValue(team);
    mocks.members.mockResolvedValue(list);
    render(<TeamView initialTeam={null} initialMembers={null} />);
    expect(screen.getByText(/Could not load your team/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("button", { name: "Invite people" })).toBeTruthy();
  });
});

describe("the invite dialog", () => {
  const sent = (email: string, status: TeamInviteResult["status"] = "sent"): TeamInviteResult => ({ email, status, invite: null });

  async function open() {
    render(<TeamView initialTeam={team} initialMembers={list} />);
    await userEvent.click(screen.getByRole("button", { name: "Invite people" }));
    return screen.getByRole("dialog", { name: "Invite people" });
  }

  it("takes addresses as chips on Enter or a comma, and offers Admin only to the owner", async () => {
    const dialog = await open();
    const input = within(dialog).getByLabelText("Email addresses");
    await userEvent.type(input, "ada@example.test,grace@example.test{Enter}not-an-email{Enter}");
    expect(within(dialog).getByText("ada@example.test")).toBeTruthy();
    expect(within(dialog).getByText("grace@example.test")).toBeTruthy();
    expect(within(dialog).queryByText("not-an-email")).toBeNull();
    const role = within(dialog).getByLabelText("Role") as HTMLSelectElement;
    expect([...role.options].map((o) => o.text)).toEqual(["Admin", "Member"]);
  });

  it("sends one request for the list with the role, then toasts the count", async () => {
    mocks.invite.mockResolvedValue({ results: [sent("ada@example.test"), sent("grace@example.test")] } satisfies TeamInvitesCreated);
    mocks.get.mockResolvedValue(team);
    mocks.members.mockResolvedValue(list);
    const dialog = await open();
    await userEvent.type(within(dialog).getByLabelText("Email addresses"), "ada@example.test, grace@example.test");
    await userEvent.selectOptions(within(dialog).getByLabelText("Role"), "member");
    await userEvent.click(within(dialog).getByRole("button", { name: "Send invites" }));
    expect(mocks.invite).toHaveBeenCalledWith(
      [
        { email: "ada@example.test", name: null },
        { email: "grace@example.test", name: null },
      ],
      "member",
    );
    expect(await screen.findByText("2 invites sent")).toBeTruthy();
  });

  it("asks for the optional Name (lead-designed) when one address is entered and sends it", async () => {
    mocks.invite.mockResolvedValue({ results: [sent("kemi@example.test")] });
    mocks.get.mockResolvedValue(team);
    mocks.members.mockResolvedValue(list);
    const dialog = await open();
    await userEvent.type(within(dialog).getByLabelText("Email addresses"), "kemi@example.test{Enter}");
    await userEvent.type(within(dialog).getByLabelText("Name (optional)"), "Kemi");
    await userEvent.click(within(dialog).getByRole("button", { name: "Send invites" }));
    expect(mocks.invite).toHaveBeenCalledWith([{ email: "kemi@example.test", name: "Kemi" }], "member");
    expect(await screen.findByText("Invite sent to kemi@example.test")).toBeTruthy();
  });

  it("keeps the addresses that could not be invited, with the reason", async () => {
    mocks.invite.mockResolvedValue({ results: [sent("ada@example.test"), sent("dan@example.test", "already_member")] });
    mocks.get.mockResolvedValue(team);
    mocks.members.mockResolvedValue(list);
    const dialog = await open();
    await userEvent.type(within(dialog).getByLabelText("Email addresses"), "ada@example.test, dan@example.test");
    await userEvent.click(within(dialog).getByRole("button", { name: "Send invites" }));
    expect(await within(dialog).findByText("dan@example.test is already on your team.")).toBeTruthy();
    expect(within(dialog).queryByText("ada@example.test")).toBeNull(); // sent: done
  });

  it("shows pilot_full as the API wrote it", async () => {
    mocks.invite.mockRejectedValue(new ApiError(409, "pilot_full", "The pilot has no free places. Ask the pilot admin."));
    const dialog = await open();
    await userEvent.type(within(dialog).getByLabelText("Email addresses"), "ada@example.test{Enter}");
    await userEvent.click(within(dialog).getByRole("button", { name: "Send invites" }));
    expect(await within(dialog).findByText("The pilot has no free places. Ask the pilot admin.")).toBeTruthy();
  });

  it("asks for at least one address", async () => {
    const dialog = await open();
    await userEvent.click(within(dialog).getByRole("button", { name: "Send invites" }));
    expect(within(dialog).getByText("Enter at least one email address.")).toBeTruthy();
    expect(mocks.invite).not.toHaveBeenCalled();
  });

  it("opens by itself with the spoken name left by the task drawer, once", async () => {
    window.sessionStorage.setItem("beyric.invite-name", "Kemi Adeyemi");
    render(<TeamView initialTeam={team} initialMembers={list} />);
    const dialog = await screen.findByRole("dialog", { name: "Invite people" });
    // The dialog can be on screen a moment before the saved name is put in its field (and the saved
    // name removed): wait for both, as a slow CI machine showed.
    await waitFor(() => {
      expect(within(dialog).getByLabelText("Name (optional)")).toHaveProperty("value", "Kemi Adeyemi");
      expect(window.sessionStorage.getItem("beyric.invite-name")).toBeNull();
    });
  });
});
