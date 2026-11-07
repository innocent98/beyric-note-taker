// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SetupWorkspace } from "@/components/auth/setup-workspace";
import { ApiError } from "@/lib/api/client";
import { markSetupPending, takeSetupPending } from "@/lib/setup-pending";

// "Set up your workspace" (Auth.dc.html:142-169): PATCH /api/team takes the name (owner or admin).
const mocks = vi.hoisted(() => ({ update: vi.fn(), connect: vi.fn(), replace: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: mocks.replace, refresh: mocks.refresh }) }));
vi.mock("@/lib/api/browser", () => ({ api: { team: { update: mocks.update }, calendar: { connect: mocks.connect } } }));

beforeEach(() => Object.values(mocks).forEach((fn) => fn.mockReset()));
afterEach(cleanup);

describe("set up your workspace", () => {
  it("draws the designer's words, the optional calendar rows and Skip for now", () => {
    render(<SetupWorkspace calendarConnected={false} />);
    expect(screen.getByText(/STEP 2 OF 2/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Set up your workspace" })).toBeTruthy();
    expect(screen.getByText("Your team, meetings and tasks live here. You can change this later.")).toBeTruthy();
    expect(screen.getByLabelText("Workspace name").getAttribute("placeholder")).toBe("e.g. Beyric");
    expect(screen.getByText("Connect your calendar")).toBeTruthy();
    expect(screen.getByText("The note taker joins upcoming meetings that have a video link.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Skip for now" }).getAttribute("href")).toBe("/");
    expect(screen.getByRole("button", { name: "Continue to Beyric" })).toBeTruthy();
  });

  it("asks for a name before saving, in the designer's words", async () => {
    render(<SetupWorkspace calendarConnected={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Continue to Beyric" }));
    expect(screen.getByText("Give your workspace a name.")).toBeTruthy();
    expect(mocks.update).not.toHaveBeenCalled();
  });

  it("renames the team and goes to the app", async () => {
    mocks.update.mockResolvedValue({});
    render(<SetupWorkspace calendarConnected={false} />);
    await userEvent.type(screen.getByLabelText("Workspace name"), "  Beyric ");
    await userEvent.click(screen.getByRole("button", { name: "Continue to Beyric" }));
    expect(mocks.update).toHaveBeenCalledWith({ name: "Beyric" });
    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/"));
  });

  it("shows the API's refusal and stays", async () => {
    mocks.update.mockRejectedValue(new ApiError(403, "team_forbidden", "Only an owner or admin can do that."));
    render(<SetupWorkspace calendarConnected={false} />);
    await userEvent.type(screen.getByLabelText("Workspace name"), "Beyric");
    await userEvent.click(screen.getByRole("button", { name: "Continue to Beyric" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Only an owner or admin can do that.");
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("saves the typed name, then opens Google's screen from the Google row", async () => {
    mocks.update.mockResolvedValue({});
    mocks.connect.mockResolvedValue({ authorize_url: "https://accounts.example/consent", show_unverified_notice: false });
    const navigate = vi.fn();
    render(<SetupWorkspace calendarConnected={false} navigate={navigate} />);
    await userEvent.type(screen.getByLabelText("Workspace name"), "Beyric");
    await userEvent.click(screen.getByRole("button", { name: /Google Calendar/ }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("https://accounts.example/consent"));
    expect(mocks.update).toHaveBeenCalledWith({ name: "Beyric" });
  });

  it("draws Microsoft Outlook switched off with one \"Not available yet.\" line", () => {
    render(<SetupWorkspace calendarConnected={false} />);
    expect((screen.getByRole("button", { name: /Microsoft Outlook/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getAllByText("Not available yet.")).toHaveLength(1);
  });

  it("shows Google as Connected, not clickable, when the calendar is already connected", () => {
    render(<SetupWorkspace calendarConnected />);
    const row = screen.getByRole("button", { name: /Google Calendar/ }) as HTMLButtonElement;
    expect(row.textContent).toContain("Connected");
    expect(row.disabled).toBe(true);
  });

  it("leaves the calendar block out while the server has the calendar off", () => {
    render(<SetupWorkspace calendarConnected={null} />);
    expect(screen.queryByText("Connect your calendar")).toBeNull();
    expect(screen.getByRole("button", { name: "Continue to Beyric" })).toBeTruthy();
  });
});

describe("the one-time mark", () => {
  it("is read once and then gone", () => {
    expect(takeSetupPending()).toBe(false);
    markSetupPending();
    expect(takeSetupPending()).toBe(true);
    expect(takeSetupPending()).toBe(false);
  });
});
