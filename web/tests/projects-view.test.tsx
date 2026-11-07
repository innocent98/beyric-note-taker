// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectsView } from "@/components/app/projects-view";
import { ApiError } from "@/lib/api/client";
import type { Project, ProjectList, SeriesList, Team } from "@/lib/api/types";
import { linkBody, projectsBody, sprintsBody } from "./helpers";

// Fed the bodies captured from the running API (docs/fe-captures/projects-w4/, cited in
// docs/fe-integration-guide-projects.md): Dan's three projects, then the member's read-only view.

const mocks = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() }));
const link = vi.hoisted(() => ({ list: vi.fn(), linkProject: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { projects: mocks, series: link } }));

const owner = projectsBody<ProjectList>("13-projects-list.json");
const member = projectsBody<ProjectList>("21-member-reads.json");
const withMilestone: ProjectList = {
  ...owner,
  projects: owner.projects.map((p) => (p.id === 2 ? { ...p, open_tasks: 2, milestone: { id: 1, name: "Client Portal launch", due_date: "2026-12-08" } } : p)),
};

beforeEach(() => [...Object.values(mocks), ...Object.values(link)].forEach((fn) => fn.mockReset()));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the Projects screen", () => {
  it("draws the title, New project, the list and the first project's fields", () => {
    render(<ProjectsView initial={withMilestone} />);
    expect(screen.getByRole("heading", { level: 1, name: "Projects" })).toBeTruthy();
    expect(screen.getByText("Tasks are filed into a project using its recurring meetings and keywords.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "New project" })).toBeTruthy();
    const list = within(screen.getByRole("navigation", { name: "Projects" }));
    expect(list.getByText("Untitled project")).toBeTruthy(); // the blank one
    expect(list.getByText("Client Portal")).toBeTruthy();
    expect(list.getAllByText("0 open tasks · 0 recurring meetings").length).toBe(2);
    for (const label of ["Name", "Colour", "Recurring meetings", "Keywords", "Sprint length", "Milestone"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.getByText("Changes save automatically")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete project" })).toBeTruthy();
  });

  it("shows another project's name, keywords, open tasks and milestone line when it is picked", async () => {
    render(<ProjectsView initial={withMilestone} />);
    await userEvent.click(screen.getByRole("button", { name: /Client Portal/ }));
    expect((screen.getByPlaceholderText("e.g. Client onboarding") as HTMLInputElement).value).toBe("Client Portal");
    expect(screen.getByText("portal")).toBeTruthy();
    expect(screen.getByText("checkout")).toBeTruthy();
    expect(screen.getByText("Client Portal launch, Tue 8 Dec 2026")).toBeTruthy();
    expect(screen.getByText("Open tasks").previousElementSibling?.textContent).toBe("2");
  });

  it("says 'None yet' when a project has no milestone", () => {
    render(<ProjectsView initial={owner} />);
    expect(screen.getByText("None yet")).toBeTruthy();
  });

  it("shows the two sprint figures from the API and the team's length in the first option", () => {
    // Capture 16 of the sprints run: a project with figures and one that follows the team's length.
    const list = sprintsBody<ProjectList>("16-projects-list.json");
    const figures: ProjectList = { ...list, projects: list.projects.map((p, i) => (i === 0 ? { ...p, open_tasks: 3, in_current_sprint: 2, in_backlog: 1 } : p)) };
    render(<ProjectsView initial={figures} team={sprintsBody<Team>("10-team.json")} />);
    expect(screen.getByText("In current sprint").previousElementSibling?.textContent).toBe("2");
    expect(screen.getByText("In backlog").previousElementSibling?.textContent).toBe("1");
    const select = screen.getByLabelText("Sprint length") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.text)).toEqual([
      "Workspace default (1 week, 5 working days)",
      "1 week, 5 working days",
      "1 week, 7 days",
      "2 weeks",
      "1 month, 30 days",
      "3 months",
      "6 months",
    ]);
    expect(select.value).toBe("");
  });

  it("names a custom team length in days, and plain 'Workspace default' without the team", () => {
    const team = { ...sprintsBody<Team>("10-team.json"), sprint_length: "custom" as const, sprint_custom_days: 12 };
    const { unmount } = render(<ProjectsView initial={sprintsBody<ProjectList>("16-projects-list.json")} team={team} />);
    expect(screen.getByRole("option", { name: "Workspace default (12 days)" })).toBeTruthy();
    unmount();
    render(<ProjectsView initial={sprintsBody<ProjectList>("16-projects-list.json")} />);
    expect(screen.getByRole("option", { name: "Workspace default" })).toBeTruthy();
  });

  it("sends the project's own length, and null for the workspace default", async () => {
    const [first] = sprintsBody<ProjectList>("16-projects-list.json").projects;
    mocks.update.mockResolvedValueOnce({ ...first, sprint_length: "week14" });
    render(<ProjectsView initial={sprintsBody<ProjectList>("16-projects-list.json")} />);
    await userEvent.selectOptions(screen.getByLabelText("Sprint length"), "2 weeks");
    expect(mocks.update).toHaveBeenCalledWith(first.id, { sprint_length: "week14" });
    await waitFor(() => expect((screen.getByLabelText("Sprint length") as HTMLSelectElement).value).toBe("week14"));
    mocks.update.mockResolvedValueOnce({ ...first, sprint_length: null });
    await userEvent.selectOptions(screen.getByLabelText("Sprint length"), "Workspace default");
    expect(mocks.update).toHaveBeenLastCalledWith(first.id, { sprint_length: null });
  });

  it("switches the Sprint length select off for a member", () => {
    render(<ProjectsView initial={member} />);
    expect((screen.getByLabelText("Sprint length") as HTMLSelectElement).disabled).toBe(true);
  });

  it("saves the name after a pause, as one PATCH of that field, and keeps what was typed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const first = owner.projects[0];
    mocks.update.mockResolvedValue({ ...first, name: "Scratch" });
    render(<ProjectsView initial={owner} />);
    const field = screen.getByPlaceholderText("e.g. Client onboarding");
    await userEvent.type(field, "Scratch");
    expect(mocks.update).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(700);
    });
    expect(mocks.update).toHaveBeenCalledTimes(1);
    expect(mocks.update).toHaveBeenCalledWith(first.id, { name: "Scratch" });
    expect((field as HTMLInputElement).value).toBe("Scratch");
    expect(within(screen.getByRole("navigation", { name: "Projects" })).getByText("Scratch")).toBeTruthy();
  });

  it("saves a colour at once", async () => {
    const first = owner.projects[0];
    mocks.update.mockResolvedValue({ ...first, color: "#8A2E5C" });
    render(<ProjectsView initial={owner} />);
    await userEvent.click(screen.getByRole("button", { name: "#8A2E5C" }));
    expect(mocks.update).toHaveBeenCalledWith(first.id, { color: "#8A2E5C" });
    await waitFor(() => expect(screen.getByRole("button", { name: "#8A2E5C" }).getAttribute("aria-pressed")).toBe("true"));
  });

  it("adds a keyword on Enter or a comma, and removes one with its x", async () => {
    const portal = owner.projects[1];
    mocks.update.mockImplementation(async (_id: number, body: { keywords: string[] }) => ({ ...portal, keywords: body.keywords }));
    render(<ProjectsView initial={owner} />);
    await userEvent.click(screen.getByRole("button", { name: /Client Portal/ }));
    const box = screen.getByLabelText("Add a keyword");
    await userEvent.type(box, "billing{Enter}");
    expect(mocks.update).toHaveBeenLastCalledWith(portal.id, { keywords: ["portal", "checkout", "billing"] });
    await screen.findByText("billing");
    await userEvent.type(screen.getByLabelText("Add a keyword"), "ops,");
    expect(mocks.update).toHaveBeenLastCalledWith(portal.id, { keywords: ["portal", "checkout", "billing", "ops"] });
    await userEvent.click(await screen.findByRole("button", { name: "Remove portal" }));
    expect(mocks.update).toHaveBeenLastCalledWith(portal.id, { keywords: ["checkout", "billing", "ops"] });
    await userEvent.type(screen.getByLabelText("Add a keyword"), "checkout{Enter}"); // already there
    expect(mocks.update).toHaveBeenCalledTimes(3);
  });

  it("adds a blank project and opens it, then deletes one with the designer's toast", async () => {
    const made: Project = { ...owner.projects[0], id: 9, name: "", color: "#1F6FB2", keywords: [] };
    mocks.create.mockResolvedValue(made);
    mocks.remove.mockResolvedValue(undefined);
    render(<ProjectsView initial={owner} />);
    await userEvent.click(screen.getByRole("button", { name: "New project" }));
    expect(mocks.create).toHaveBeenCalledWith({});
    await waitFor(() => expect(within(screen.getByRole("navigation", { name: "Projects" })).getAllByText("Untitled project").length).toBe(2));
    await userEvent.click(screen.getByRole("button", { name: "Delete project" }));
    expect(mocks.remove).toHaveBeenCalledWith(9);
    await screen.findByText("Project deleted. Its tasks now have no project.");
    expect(within(screen.getByRole("navigation", { name: "Projects" })).queryAllByText("Untitled project").length).toBe(1);
  });

  it("shows a refused change as an error", async () => {
    mocks.update.mockRejectedValue(new ApiError(403, "team_forbidden", "Only an owner or admin of the team can do that."));
    render(<ProjectsView initial={owner} />);
    await userEvent.click(screen.getByRole("button", { name: "#3C4A7A" }));
    expect(await screen.findByText("Only an owner or admin of the team can do that.")).toBeTruthy();
  });

  it("gives a member the screen with every control off: no New project, no Delete, no keyword box", () => {
    render(<ProjectsView initial={member} />);
    expect(screen.queryByRole("button", { name: "New project" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete project" })).toBeNull();
    expect(screen.queryByLabelText("Add a keyword")).toBeNull();
    expect(screen.queryByText("Changes save automatically")).toBeNull();
    expect((screen.getByPlaceholderText("e.g. Client onboarding") as HTMLInputElement).readOnly).toBe(true);
    expect((screen.getByRole("button", { name: "#3C4A7A" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Only an owner or admin of your team can change projects.")).toBeTruthy();
  });

  it("says so when there are no projects, and when the list could not be read", async () => {
    const { unmount } = render(<ProjectsView initial={{ projects: [], can_manage: true }} />);
    expect(screen.getByText("No projects yet.")).toBeTruthy();
    unmount();
    mocks.list.mockResolvedValue(owner);
    link.list.mockResolvedValue({ timezone: "UTC", series: [] });
    render(<ProjectsView initial={null} />);
    expect(screen.getByText(/Could not load projects/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("navigation", { name: "Projects" });
  });
});

// Recurring meetings: fed the captures of docs/fe-captures/series-project-link/ (Client Portal, id 1, has
// both recurring meetings; Beyric Note Taker, id 2, none) and their answers to the link calls.
describe("the Recurring meetings checklist", () => {
  const projects = linkBody<ProjectList>("17-projects-linked.json");
  const memberView = linkBody<ProjectList>("18-projects-member.json");
  const cards = linkBody<SeriesList>("16-cards-linked.json");
  const [planning, review] = cards.series;
  const notetaker = projects.projects[1];

  it("draws a row per recurring meeting with its cadence, ticked when it is linked to this project", () => {
    render(<ProjectsView initial={projects} series={cards} />);
    const group = within(screen.getByRole("group", { name: "Recurring meetings" }));
    const rows = group.getAllByRole("checkbox");
    expect(rows.map((r) => r.getAttribute("aria-checked"))).toEqual(["true", "true"]);
    expect(group.getByText("Beyric Monday planning")).toBeTruthy();
    expect(group.getByText("Weekly on Mondays, 14:00")).toBeTruthy();
    expect(group.getByText("Beyric Friday review")).toBeTruthy();
    expect(group.getByText("Weekly on Fridays, 14:00")).toBeTruthy();
    expect(screen.getByText("Action items from these meetings go into this project.")).toBeTruthy();
    expect(screen.queryByText("Not available yet.")).toBeNull();
  });

  it("counts them in the list: 'N open tasks · M recurring meetings', singular for one", () => {
    const one: SeriesList = { ...cards, series: [planning, { ...review, project: null }] };
    render(<ProjectsView initial={projects} series={one} />);
    const list = within(screen.getByRole("navigation", { name: "Projects" }));
    expect(list.getByText("0 open tasks · 1 recurring meeting")).toBeTruthy();
    expect(list.getByText("0 open tasks · 0 recurring meetings")).toBeTruthy();
  });

  it("says 'Linked to {project}' on another project's rows, and leaves them unticked", async () => {
    render(<ProjectsView initial={projects} series={cards} />);
    await userEvent.click(screen.getByRole("button", { name: /Beyric Note Taker/ }));
    const rows = within(screen.getByRole("group", { name: "Recurring meetings" })).getAllByRole("checkbox");
    expect(rows.map((r) => r.getAttribute("aria-checked"))).toEqual(["false", "false"]);
    expect(within(rows[0]).getByText("Linked to Client Portal")).toBeTruthy();
    expect(within(rows[1]).getByText("Linked to Client Portal")).toBeTruthy();
  });

  it("links a row to this project (moving it from the other), and moves the counts", async () => {
    link.linkProject.mockResolvedValueOnce({ ...planning, project: { id: notetaker.id, name: notetaker.name } });
    render(<ProjectsView initial={projects} series={cards} />);
    await userEvent.click(screen.getByRole("button", { name: /Beyric Note Taker/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Beyric Monday planning/ }));
    expect(link.linkProject).toHaveBeenCalledWith(planning.id, notetaker.id);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /Beyric Monday planning/ }).getAttribute("aria-checked")).toBe("true"));
    const list = within(screen.getByRole("navigation", { name: "Projects" }));
    expect(list.getAllByText(/recurring meeting/).map((e) => e.textContent)).toEqual([
      "0 open tasks · 1 recurring meeting", // Beyric Note Taker
      "0 open tasks · 1 recurring meeting", // Client Portal lost one
    ]);
  });

  it("unlinks a ticked row with null", async () => {
    link.linkProject.mockResolvedValueOnce({ ...planning, project: null });
    render(<ProjectsView initial={projects} series={cards} />);
    await userEvent.click(screen.getByRole("checkbox", { name: /Beyric Monday planning/ }));
    expect(link.linkProject).toHaveBeenCalledWith(planning.id, null);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /Beyric Monday planning/ }).getAttribute("aria-checked")).toBe("false"));
  });

  it("toggles from the keyboard", async () => {
    link.linkProject.mockResolvedValueOnce({ ...planning, project: null });
    render(<ProjectsView initial={projects} series={cards} />);
    screen.getByRole("checkbox", { name: /Beyric Monday planning/ }).focus();
    await userEvent.keyboard(" ");
    expect(link.linkProject).toHaveBeenCalledWith(planning.id, null);
  });

  it("shows the API's refusal and leaves the tick as it was", async () => {
    link.linkProject.mockRejectedValueOnce(new ApiError(403, "team_forbidden", "You can't do that here. Ask an owner or admin of your team."));
    render(<ProjectsView initial={projects} series={cards} />);
    await userEvent.click(screen.getByRole("checkbox", { name: /Beyric Monday planning/ }));
    expect(await screen.findByText(/Ask an owner or admin of your team/)).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: /Beyric Monday planning/ }).getAttribute("aria-checked")).toBe("true");
  });

  it("shows a member the ticks, with no way to change them", async () => {
    render(<ProjectsView initial={memberView} series={cards} />);
    const rows = screen.getAllByRole("checkbox");
    expect(rows.map((r) => r.getAttribute("aria-checked"))).toEqual(["true", "true"]);
    expect(rows.every((r) => r.getAttribute("aria-disabled") === "true")).toBe(true);
    await userEvent.click(rows[0]);
    expect(link.linkProject).not.toHaveBeenCalled();
  });

  it("draws no checklist box when the team has no recurring meeting", () => {
    render(<ProjectsView initial={projects} series={{ ...cards, series: [] }} />);
    expect(screen.getAllByText("Recurring meetings").length).toBeGreaterThan(0);
    expect(screen.queryByRole("group", { name: "Recurring meetings" })).toBeNull();
  });

  it("shows 'Could not load projects' when the recurring meetings could not be read, and Try again reloads both", async () => {
    link.list.mockResolvedValue(cards);
    mocks.list.mockResolvedValue(projects);
    render(<ProjectsView initial={null} series={null} />);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("group", { name: "Recurring meetings" })).toBeTruthy();
    expect(link.list).toHaveBeenCalledTimes(1);
  });
});
