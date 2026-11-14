// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TeamsView, createErrorMessage } from "@/components/app/teams-view";
import { TEAM_STORAGE_KEY } from "@/lib/active-team";
import { ApiError } from "@/lib/api/client";
import type { TeamList, TeamSummary } from "@/lib/api/types";
import { teamsBody } from "./helpers";

// The Teams screen (lead-designed): Pat's list from the real capture 46, creating a team (captures 48,
// 49 and 51: 201, 422 `invalid_name`, 409 `team_limit` in the guide's words).

const mocks = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: mocks.refresh, push: vi.fn() }),
  usePathname: () => "/teams",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api/browser", () => ({ api: { teams: { list: mocks.list, create: mocks.create } } }));

const list = teamsBody<TeamList>("46-teams-list.json");
const created = teamsBody<TeamSummary>("48-teams-create.json");
const limit = teamsBody<{ error: { code: string; message: string } }>("51-teams-create-over-limit.json").error;
const emptyName = teamsBody<{ error: { code: string; message: string } }>("49-teams-create-empty-name.json").error;

// Following a link in jsdom would try to navigate; the click still reaches the handlers.
document.addEventListener("click", (event) => {
  if (event.target instanceof Element && event.target.closest("a[href]")) event.preventDefault();
});

beforeEach(() => {
  cleanup();
  window.sessionStorage.clear();
  Object.values(mocks).forEach((fn) => fn.mockReset());
});

const rows = () => within(screen.getByRole("list", { name: "Your teams" })).getAllByRole("listitem");

describe("the list", () => {
  it("shows every team with Personal or the role, and how many people", () => {
    render(<TeamsView initial={list} />);
    const text = rows().map((r) => r.textContent);
    expect(text[0]).toContain("My team");
    expect(text[0]).toContain("Personal");
    expect(text[0]).toContain("Only you");
    expect(text[1]).toContain("Platform team");
    expect(text[1]).toContain("Owner");
    expect(text[1]).toContain("1 member");
    expect(text[2]).toContain("Client Y");
    expect(text[2]).toContain("Member");
    expect(text[2]).toContain("2 members");
  });

  it("marks the team this tab works on (the default until another is chosen)", () => {
    render(<TeamsView initial={list} />);
    expect(rows()[0].textContent).toContain("Working in this team");
    expect(rows()[1].textContent).not.toContain("Working in this team");
  });

  it("Open goes to that team's Team page and remembers it for this tab; the default team forgets the choice", () => {
    render(<TeamsView initial={list} />);
    const open = (name: string) => screen.getByRole("link", { name }) as HTMLAnchorElement;
    expect(open("Open Platform team").getAttribute("href")).toBe("/team?team=4");
    fireEvent.click(open("Open Platform team"));
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBe(":4");
    expect(open("Open My team").getAttribute("href")).toBe("/team");
    fireEvent.click(open("Open My team"));
    expect(window.sessionStorage.getItem(TEAM_STORAGE_KEY)).toBeNull();
  });

  it("a failed load is a line with Try again", async () => {
    mocks.list.mockResolvedValue(list);
    render(<TeamsView initial={null} />);
    expect(screen.getByRole("alert").textContent).toContain("Could not load your teams.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("list", { name: "Your teams" })).toBeTruthy();
  });
});

describe("making a team", () => {
  const name = () => screen.getByLabelText("Team name") as HTMLInputElement;
  const submit = () => screen.getByRole("button", { name: "Make team" });

  it("needs a name, says how many more can be made, and sends the name", async () => {
    mocks.create.mockResolvedValue(created);
    mocks.list.mockResolvedValue({ ...list, teams: [...list.teams, created], can_create_more: 1 });
    render(<TeamsView initial={list} />);
    expect((submit() as HTMLButtonElement).disabled).toBe(true);
    expect(document.body.textContent).toContain("You can make 2 more.");
    fireEvent.change(name(), { target: { value: "  Side project " } });
    fireEvent.click(submit());
    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith("  Side project "));
    expect(await screen.findByText("Side project")).toBeTruthy();
    expect(name().value).toBe("");
    expect(mocks.refresh).toHaveBeenCalled(); // the switcher is drawn by the server: ask for it again
    expect(document.body.textContent).toContain("You can make 1 more.");
  });

  it("shows the limit in the guide's own words, and reads the list again", async () => {
    expect(limit).toEqual({ code: "team_limit", message: "You can own up to 3 teams." });
    mocks.create.mockRejectedValue(new ApiError(409, limit.code, limit.message));
    mocks.list.mockResolvedValue({ ...list, can_create_more: 0 });
    render(<TeamsView initial={list} />);
    fireEvent.change(name(), { target: { value: "One too many" } });
    fireEvent.click(submit());
    expect((await screen.findByRole("alert")).textContent).toContain("You can own up to 3 teams.");
    await waitFor(() => expect(mocks.list).toHaveBeenCalled());
  });

  it("when three are owned the form says so and cannot be used", () => {
    render(<TeamsView initial={{ ...list, can_create_more: 0 }} />);
    expect(document.body.textContent).toContain("You already own 3 teams, the most one account can have.");
    expect(name().disabled).toBe(true);
    expect((submit() as HTMLButtonElement).disabled).toBe(true);
  });

  it("an empty name from the server (422) shows its own message, which errorMessage would hide", () => {
    expect(emptyName.message).toBe("Give the team a name.");
    expect(createErrorMessage(new ApiError(422, emptyName.code, emptyName.message))).toBe("Give the team a name.");
    expect(createErrorMessage(new ApiError(404, "not_found", "No such team."))).toBe("Something went wrong. Please try again.");
  });

  it("limits the name to 100 characters, as the server does", () => {
    render(<TeamsView initial={list} />);
    expect(name().maxLength).toBe(100);
  });
});
