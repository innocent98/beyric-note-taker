// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { autoShareErrorMessage, SettingsAutoShare } from "@/components/app/settings-auto-share";
import { ApiError } from "@/lib/api/client";
import type { AutoShare, TeamList } from "@/lib/api/types";
import { teamsBody, teamsCapture } from "./helpers";

// "Share my new meetings with" (lead-designed). Teams are Pat's (capture 46); the bodies and errors are the
// guide's section 12 (captures 66 read, 67 set, 68 personal 409, 69 not my team 404, 70 clear).

const mocks = vi.hoisted(() => ({ set: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { settings: { autoShare: mocks } } }));

const teams = teamsBody<TeamList>("46-teams-list.json").teams;
const set = teamsBody<AutoShare>("67-auto-share-set.json");
const cleared = teamsBody<AutoShare>("70-auto-share-clear.json");
const personal = teamsBody<{ error: { code: string; message: string } }>("68-auto-share-personal.json").error;
const stranger = teamsBody<{ error: { code: string; message: string } }>("69-auto-share-not-my-team.json").error;

beforeEach(() => {
  cleanup();
  mocks.set.mockReset();
});
const select = () => screen.getByRole("combobox", { name: "Share my new meetings with" }) as HTMLSelectElement;

describe("the card", () => {
  it("lists No team and every non-personal team, never the personal space, with the saved choice selected", () => {
    render(<SettingsAutoShare initial={5} teams={teams} />);
    expect(within(select()).getAllByRole("option").map((o) => o.textContent)).toEqual(["No team (keep private)", "Platform team", "Client Y"]);
    expect(select().value).toBe("5");
  });

  it("starts on No team when nothing is chosen, or the chosen team is no longer the person's", () => {
    const { unmount } = render(<SettingsAutoShare initial={null} teams={teams} />);
    expect(select().value).toBe("none");
    unmount();
    render(<SettingsAutoShare initial={99} teams={teams} />);
    expect(select().value).toBe("none");
  });

  it("says in one place how it meets the team-wide switch, and that the choice is the person's own", () => {
    render(<SettingsAutoShare initial={null} teams={teams} />);
    expect(document.body.textContent).toContain("This is your choice for your own meetings. It is used instead of the team-wide");
    expect(document.body.textContent).toContain("Never when the bot joins, and never again for a meeting you unshared.");
  });

  it("with no team to choose, points to making one", () => {
    render(<SettingsAutoShare initial={null} teams={teams.filter((t) => t.is_personal)} />);
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByRole("link", { name: "Make a team" }).getAttribute("href")).toBe("/teams");
  });
});

describe("saving", () => {
  it("saves a team at once and confirms in plain words", async () => {
    mocks.set.mockResolvedValue(set);
    render(<SettingsAutoShare initial={null} teams={teams} />);
    fireEvent.change(select(), { target: { value: "5" } });
    await waitFor(() => expect(mocks.set).toHaveBeenCalledWith(5));
    expect((await screen.findByRole("status")).textContent).toContain("New meetings will be shared with Client Y");
  });

  it("No team sends null and says new meetings stay private", async () => {
    mocks.set.mockResolvedValue(cleared);
    render(<SettingsAutoShare initial={5} teams={teams} />);
    fireEvent.change(select(), { target: { value: "none" } });
    await waitFor(() => expect(mocks.set).toHaveBeenCalledWith(null));
    expect((await screen.findByRole("status")).textContent).toContain("New meetings will stay private");
  });

  it.each([
    [new ApiError(409, personal.code, personal.message), "Choose a team. Your personal space is only yours."],
    [new ApiError(404, stranger.code, stranger.message), "You are no longer in that team. Choose another."],
    [new ApiError(409, "multi_team_disabled", "Choosing a team for new meetings is not available yet."), "Choosing a team for new meetings is not available yet."],
  ])("a refusal shows the guide's words and puts the choice back", async (error, shown) => {
    mocks.set.mockRejectedValue(error);
    render(<SettingsAutoShare initial={4} teams={teams} />);
    fireEvent.change(select(), { target: { value: "5" } });
    expect((await screen.findByRole("alert")).textContent).toContain(shown);
    expect(select().value).toBe("4");
  });

  it("any other failure is the plain default", () => {
    expect(autoShareErrorMessage(new ApiError(404, "not_found", "No such meeting"))).toBe("Something went wrong. Please try again.");
    expect(teamsCapture("68-auto-share-personal.json").response.status).toBe(409);
  });
});
