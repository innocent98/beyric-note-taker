// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsPlannedSections } from "@/components/app/settings-planned-sections";
import { ApiError } from "@/lib/api/client";
import type { SlackState, TasksSettings } from "@/lib/api/types";
import { integrationsBody, integrationsError, tasksBody } from "./helpers";

const mocks = vi.hoisted(() => ({ connect: vi.fn(), disconnect: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    settings: { tasks: { set: vi.fn() } },
    integrations: { slack: { connect: mocks.connect, disconnect: mocks.disconnect } },
  },
}));

const tasks = tasksBody<TasksSettings>("75-settings-tasks.json");
const off = integrationsBody<SlackState>("05-slack-not-connected.json");
const on = integrationsBody<SlackState>("10-slack-connected.json");
const failing = integrationsBody<SlackState>("15-slack-failing.json");
const member = integrationsBody<SlackState>("11-slack-as-member.json");
const form = () => screen.getByLabelText("Slack webhook address").closest("form") as HTMLElement;
const card = () => screen.getByRole("heading", { name: "Tasks" }).closest("section") as HTMLElement;

beforeEach(() => {
  mocks.connect.mockReset();
  mocks.disconnect.mockReset();
});
afterEach(cleanup);

describe("Settings > Tasks > Post tasks to Slack", () => {
  it("says only the project tool is not available once Slack is live", () => {
    render(<SettingsPlannedSections tasks={tasks} slack={off} />);
    expect(within(card()).getByText("Sync with your project tool is not available yet.")).toBeTruthy();
    expect((within(card()).getByRole("button", { name: "Connect Slack" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("opens the address field, sends it once and shows the connected state without the address", async () => {
    mocks.connect.mockResolvedValue(on);
    render(<SettingsPlannedSections tasks={tasks} slack={off} />);
    await userEvent.click(within(card()).getByRole("button", { name: "Connect Slack" }));
    const field = screen.getByLabelText("Slack webhook address") as HTMLInputElement;
    expect(field.type).toBe("password");
    await userEvent.type(field, ["https://hooks.slack.com", "services", "TFAKEFAKE", "BFAKEFAKE", "FAKEFAKEFAKEFAKEFAKEFAKE"].join("/"));
    await userEvent.click(within(form()).getByRole("button", { name: "Connect" }));
    expect(mocks.connect).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(within(card()).getByText("Slack connected")).toBeTruthy());
    expect(screen.queryByLabelText("Slack webhook address")).toBeNull();
    expect(document.body.innerHTML).not.toContain("FAKEFAKE");
  });

  it("shows the server's sentence and keeps the field when Slack refuses the address", async () => {
    const refused = integrationsError("07-slack-refused-by-slack.json");
    mocks.connect.mockRejectedValue(new ApiError(refused.status, refused.code, refused.message));
    render(<SettingsPlannedSections tasks={tasks} slack={off} />);
    await userEvent.click(within(card()).getByRole("button", { name: "Connect Slack" }));
    await userEvent.type(screen.getByLabelText("Slack webhook address"), "x");
    await userEvent.click(within(form()).getByRole("button", { name: "Connect" }));
    expect(await screen.findByText(refused.message)).toBeTruthy();
    expect(screen.getByLabelText("Slack webhook address")).toBeTruthy();
  });

  it("disconnects", async () => {
    mocks.disconnect.mockResolvedValue(undefined);
    render(<SettingsPlannedSections tasks={tasks} slack={on} />);
    await userEvent.click(within(card()).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(within(card()).getByRole("button", { name: "Connect Slack" })).toBeTruthy());
  });

  it("shows a failing connection with the fixed sentence", () => {
    render(<SettingsPlannedSections tasks={tasks} slack={failing} />);
    expect(within(card()).getByText("Slack needs attention")).toBeTruthy();
    expect(within(card()).getByText(failing.last_error as string)).toBeTruthy();
  });

  it("lets a teammate look but not change", () => {
    render(<SettingsPlannedSections tasks={tasks} slack={member} />);
    expect(within(card()).getByText("Only owners and admins can change this.")).toBeTruthy();
    expect(within(card()).queryByRole("button", { name: "Disconnect" })).toBeNull();
  });

  it("stays drawn off when the state could not be read", () => {
    render(<SettingsPlannedSections tasks={tasks} slack={null} />);
    expect((within(card()).getByRole("button", { name: "Connect Slack" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
