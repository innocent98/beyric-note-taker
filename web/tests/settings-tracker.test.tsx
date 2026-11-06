// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsPlannedSections } from "@/components/app/settings-planned-sections";
import { ApiError } from "@/lib/api/client";
import type { LinearState, SlackState, TasksSettings } from "@/lib/api/types";
import { integrationsBody, integrationsError, tasksBody } from "./helpers";

const mocks = vi.hoisted(() => ({ teams: vi.fn(), connect: vi.fn(), disconnect: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    settings: { tasks: { set: vi.fn() } },
    integrations: {
      slack: { connect: vi.fn(), disconnect: vi.fn() },
      linear: { teams: mocks.teams, connect: mocks.connect, disconnect: mocks.disconnect },
    },
  },
}));

const tasks = tasksBody<TasksSettings>("75-settings-tasks.json");
const slack = integrationsBody<SlackState>("18-slack-after-disconnect.json");
const off = integrationsBody<LinearState>("19-linear-not-connected.json");
const on = integrationsBody<LinearState>("26-linear-connected.json");
const failing = integrationsBody<LinearState>("30-linear-failing.json");
const member = integrationsBody<LinearState>("27-linear-as-member.json");
const found = integrationsBody<{ teams: { id: string; key: string; name: string }[] }>("23-linear-teams.json");
const card = () => screen.getByRole("heading", { name: "Tasks" }).closest("section") as HTMLElement;
const form = () => screen.getByLabelText("Linear personal API key").closest("form") as HTMLElement;
const KEY = ["lin_api", "FAKEFAKEFAKEFAKEFAKEFAKE"].join("_");

beforeEach(() => {
  mocks.teams.mockReset();
  mocks.connect.mockReset();
  mocks.disconnect.mockReset();
});
afterEach(cleanup);

describe("Settings > Tasks > Sync with your project tool (Linear)", () => {
  it("drops the not-available line once both rows are live and keeps the other tools unselectable", () => {
    render(<SettingsPlannedSections tasks={tasks} slack={slack} linear={off} />);
    expect(within(card()).queryByText(/not available yet/)).toBeNull();
    const select = within(card()).getByRole("combobox", { name: "Project tool" }) as HTMLSelectElement;
    expect(select.value).toBe("Linear");
    expect(Array.from(select.options).filter((o) => o.disabled).map((o) => o.value)).toEqual(["Jira", "Asana", "Taiga"]);
  });

  it("asks for the key, lists the teams, connects and never keeps the key on screen", async () => {
    mocks.teams.mockResolvedValue(found);
    mocks.connect.mockResolvedValue(on);
    render(<SettingsPlannedSections tasks={tasks} slack={slack} linear={off} />);
    await userEvent.click(within(card()).getByRole("button", { name: "Connect" }));
    const field = screen.getByLabelText("Linear personal API key") as HTMLInputElement;
    expect(field.type).toBe("password");
    await userEvent.type(field, KEY);
    await userEvent.click(within(form()).getByRole("button", { name: "Find teams" }));
    expect(mocks.teams).toHaveBeenCalledWith(KEY);
    await userEvent.selectOptions(await screen.findByLabelText("Linear team"), found.teams[1].id);
    await userEvent.click(within(form()).getByRole("button", { name: "Connect" }));
    expect(mocks.connect).toHaveBeenCalledWith(KEY, found.teams[1].id);
    await waitFor(() => expect(within(card()).getByText(/Syncing with Linear/)).toBeTruthy());
    expect(document.body.innerHTML).not.toContain("FAKEFAKE");
  });

  it("shows the server's sentence when the key is refused", async () => {
    const refused = integrationsError("21-linear-teams-rejected.json");
    mocks.teams.mockRejectedValue(new ApiError(refused.status, refused.code, refused.message));
    render(<SettingsPlannedSections tasks={tasks} slack={slack} linear={off} />);
    await userEvent.click(within(card()).getByRole("button", { name: "Connect" }));
    await userEvent.type(screen.getByLabelText("Linear personal API key"), KEY);
    await userEvent.click(within(form()).getByRole("button", { name: "Find teams" }));
    expect(await screen.findByText(refused.message)).toBeTruthy();
    expect(screen.queryByLabelText("Linear team")).toBeNull();
  });

  it("disconnects", async () => {
    mocks.disconnect.mockResolvedValue(undefined);
    render(<SettingsPlannedSections tasks={tasks} slack={slack} linear={on} />);
    expect(within(card()).getByText(`Syncing with Linear · ${on.team_name}`)).toBeTruthy();
    await userEvent.click(within(card()).getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(within(card()).getByRole("button", { name: "Connect" })).toBeTruthy());
  });

  it("shows a failing connection with the fixed sentence", () => {
    render(<SettingsPlannedSections tasks={tasks} slack={slack} linear={failing} />);
    expect(within(card()).getByText("Linear needs attention")).toBeTruthy();
    expect(within(card()).getByText(failing.last_error as string)).toBeTruthy();
  });

  it("lets a teammate look but not change", () => {
    render(<SettingsPlannedSections tasks={tasks} slack={slack} linear={member} />);
    expect(within(card()).getAllByText("Only owners and admins can change this.").length).toBeGreaterThan(0);
    expect(within(card()).queryByRole("button", { name: "Disconnect" })).toBeNull();
  });

  it("stays drawn off, and named in the one line, when the state could not be read", () => {
    render(<SettingsPlannedSections tasks={tasks} slack={slack} linear={null} />);
    expect(within(card()).getByText("Sync with your project tool is not available yet.")).toBeTruthy();
    expect((within(card()).getByRole("button", { name: "Connect" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
