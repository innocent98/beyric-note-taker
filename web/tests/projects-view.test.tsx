// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectsView } from "@/components/app/projects-view";
import { ApiError } from "@/lib/api/client";
import type { Project, ProjectList } from "@/lib/api/types";
import { projectsBody } from "./helpers";

// Fed the bodies captured from the running API (docs/fe-captures/projects-w4/, cited in
// docs/fe-integration-guide-projects.md): Dan's three projects, then the member's read-only view.

const mocks = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { projects: mocks } }));

const owner = projectsBody<ProjectList>("13-projects-list.json");
const member = projectsBody<ProjectList>("21-member-reads.json");
const withMilestone: ProjectList = {
  ...owner,
  projects: owner.projects.map((p) => (p.id === 2 ? { ...p, open_tasks: 2, milestone: { id: 1, name: "Client Portal launch", due_date: "2026-12-08" } } : p)),
};

beforeEach(() => Object.values(mocks).forEach((fn) => fn.mockReset()));
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
    expect(list.getAllByText("0 open tasks").length).toBe(2);
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

  it("switches off Recurring meetings, Sprint length and the two sprint figures with 'Not available yet' lines", () => {
    render(<ProjectsView initial={owner} />);
    expect((screen.getByLabelText("Sprint length") as HTMLSelectElement).disabled).toBe(true);
    expect(screen.getAllByText("Not available yet.").length).toBe(2);
    expect(screen.getByText("The sprint figures are not available yet.")).toBeTruthy();
    expect(screen.getByText("In current sprint")).toBeTruthy();
    expect(screen.getByText("In backlog")).toBeTruthy();
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
    render(<ProjectsView initial={null} />);
    expect(screen.getByText(/Could not load projects/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByRole("navigation", { name: "Projects" });
  });
});
