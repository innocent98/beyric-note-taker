// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TeamsView, createErrorMessage } from "@/components/app/teams-view";
import { TEAM_STORAGE_KEY } from "@/lib/active-team";
import { ApiError } from "@/lib/api/client";
import type { TeamList, TeamSummary } from "@/lib/api/types";
import { teamsBody, lifecycleBody } from "./helpers";

// The Teams screen (lead-designed): Pat's list from the real capture 46, creating a team (captures 48,
// 49 and 51: 201, 422 `invalid_name`, 409 `team_limit` in the guide's words).

const mocks = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: mocks.refresh, push: vi.fn() }),
  usePathname: () => "/teams",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api/browser", () => ({ api: { teams: { list: mocks.list, create: mocks.create } } }));

// The rows come from Pat's list (capture 46: before the plan limit existed, so no `kind` or limit fields); the
// plan limit, the class option and the upgrade message from the workspaces captures
// (docs/fe-captures/workspaces-lifecycle/, `scripts/capture_workspaces_lifecycle.py`): Ada on the 20-hour plan (09), the
// same person at her limit (13), Pat on Free (07) and the refusals (08, 14).
const rows46 = teamsBody<TeamList>("46-teams-list.json");
const created = teamsBody<TeamSummary>("48-teams-create.json");
const emptyName = teamsBody<{ error: { code: string; message: string } }>("49-teams-create-empty-name.json").error;
const starter = lifecycleBody<TeamList>("09-ada-teams-starter.json");
const atLimit = lifecycleBody<TeamList>("13-ada-teams-at-limit.json");
const free = lifecycleBody<TeamList>("07-pat-teams-free.json");
const createdClass = lifecycleBody<TeamSummary>("10-ada-create-class.json");
const limit = lifecycleBody<{ error: { code: string; message: string } }>("14-ada-create-over-limit.json").error;
const freeLimit = lifecycleBody<{ error: { code: string; message: string } }>("08-pat-create-team-free.json").error;
// Pat's rows with Ada's plan state: what a Starter person's screen reads.
const list: TeamList = {
  ...rows46,
  can_create_more: starter.can_create_more,
  owned_teams: starter.owned_teams,
  max_owned_teams: starter.max_owned_teams,
  plan_name: starter.plan_name,
  limit_message: starter.limit_message,
};

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

  it("says in one line what a team is for, near the form", () => {
    render(<TeamsView initial={list} />);
    expect(screen.getByText("A team can be just you. Teams can share meetings and connect Slack and Linear.")).toBeTruthy();
  });

  it("needs a name, says what the plan allows, and sends the name as a team", async () => {
    mocks.create.mockResolvedValue(created);
    mocks.list.mockResolvedValue({ ...list, teams: [...list.teams, created], can_create_more: 1, owned_teams: 2 });
    render(<TeamsView initial={list} />);
    expect((submit() as HTMLButtonElement).disabled).toBe(true);
    expect(document.body.textContent).toContain("You own 1 of the 3 teams your plan allows. You can make 2 more.");
    fireEvent.change(name(), { target: { value: "  Side project " } });
    fireEvent.click(submit());
    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith("  Side project ", "team"));
    expect(await screen.findByText("Side project")).toBeTruthy();
    expect(name().value).toBe("");
    expect(mocks.refresh).toHaveBeenCalled(); // the switcher is drawn by the server: ask for it again
    expect(document.body.textContent).toContain("You own 2 of the 3 teams your plan allows. You can make 1 more.");
  });

  it("'This is a class' makes a class: the button and the toast say so, and the request carries the kind", async () => {
    expect(createdClass.kind).toBe("class");
    mocks.create.mockResolvedValue(createdClass);
    mocks.list.mockResolvedValue({ ...list, teams: [...list.teams, createdClass] });
    render(<TeamsView initial={list} />);
    fireEvent.click(screen.getByLabelText(/This is a class/));
    fireEvent.change(name(), { target: { value: "Maths 101" } });
    fireEvent.click(screen.getByRole("button", { name: "Make class" }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith("Maths 101", "class"));
    expect(await screen.findByText("Class made")).toBeTruthy();
    expect((screen.getByLabelText(/This is a class/) as HTMLInputElement).checked).toBe(false); // ready for the next
  });

  it("a class in the list says Class, and its owner is the Lecturer", () => {
    render(<TeamsView initial={{ ...list, teams: [...list.teams, createdClass] }} />);
    const row = rows().find((r) => r.textContent?.includes("Maths 101"))!;
    expect(row.textContent).toContain("Class");
    expect(row.textContent).toContain("Lecturer");
  });

  it("shows the limit in the API's own words, and reads the list again", async () => {
    expect(limit.code).toBe("team_limit");
    expect(limit.message).toBe("Your plan owns up to 3 teams and you have 3. Upgrade to the Pro plan to own up to 10 teams.");
    mocks.create.mockRejectedValue(new ApiError(409, limit.code, limit.message));
    mocks.list.mockResolvedValue(atLimit);
    render(<TeamsView initial={list} />);
    fireEvent.change(name(), { target: { value: "One too many" } });
    fireEvent.click(submit());
    expect((await screen.findByRole("alert")).textContent).toContain(limit.message);
    await waitFor(() => expect(mocks.list).toHaveBeenCalled());
  });

  it("at the limit the card says why in the same words, links to the plans, and the form cannot be used", () => {
    render(<TeamsView initial={atLimit} />);
    expect(document.body.textContent).toContain(atLimit.limit_message!);
    const plans = screen.getByRole("link", { name: "See plans" }) as HTMLAnchorElement;
    expect(plans.getAttribute("href")).toBe("/billing/plans");
    expect(name().disabled).toBe(true);
    expect((screen.getByLabelText(/This is a class/) as HTMLInputElement).disabled).toBe(true);
    expect((submit() as HTMLButtonElement).disabled).toBe(true);
  });

  it("on Free the card says an owned team needs a plan, naming the plan to upgrade to", () => {
    expect(freeLimit.message).toBe(free.limit_message);
    expect(freeLimit.message).toBe("Owning a team needs a paid plan. Upgrade to the 20 hours a month plan to own up to 3 teams.");
    render(<TeamsView initial={free} />);
    expect(document.body.textContent).toContain(free.limit_message!);
    expect(document.body.textContent).toContain("You own 0. Your plan does not include owning a team.");
    expect(screen.getByRole("link", { name: "See plans" })).toBeTruthy();
    expect((submit() as HTMLButtonElement).disabled).toBe(true);
  });

  it("a plan with no limit says so and is never at the limit", () => {
    render(<TeamsView initial={{ ...list, max_owned_teams: null, can_create_more: null, limit_message: null }} />);
    expect(document.body.textContent).toContain("You own 1. Your plan has no limit.");
    expect(screen.queryByRole("link", { name: "See plans" })).toBeNull();
    expect(name().disabled).toBe(false);
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
