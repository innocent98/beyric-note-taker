// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsWorkspaceBotName } from "@/components/app/settings-workspace-bot-name";
import { ApiError } from "@/lib/api/client";

// The lead-designed "Note taker name in this workspace" card: the person's own name for ONE workspace,
// empty meaning their default from Personal settings.

const mocks = vi.hoisted(() => ({ put: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { workspacePrefs: { put: mocks.put } } }));

const PREFS = {
  team_id: 7,
  bot_name: null,
  bot_name_default: "Pat default",
  warning: null,
  auto_join_rule: "all" as const,
  join_unanswered: false,
  skip_one_on_ones: false,
  calendar_template_id: null,
};
const field = () => screen.getByRole("textbox", { name: "Note taker name in this workspace" }) as HTMLInputElement;

beforeEach(() => mocks.put.mockReset());
afterEach(cleanup);

describe("the card", () => {
  it("names the workspace and the default that applies when it is empty", () => {
    render(<SettingsWorkspaceBotName initial={PREFS} workspaceName="Luran" />);
    expect(screen.getByRole("heading", { level: 2, name: "Note taker name in this workspace" })).toBeTruthy();
    expect(screen.getByText(/in Luran\. Leave it empty to use “/)).toBeTruthy();
    expect(field().placeholder).toBe("Pat default");
  });

  it("saves the typed name for the workspace and keeps what the server answers", async () => {
    mocks.put.mockResolvedValue({ ...PREFS, bot_name: "Pat at Luran" });
    render(<SettingsWorkspaceBotName initial={PREFS} workspaceName="Luran" />);
    await userEvent.type(field(), "Pat at Luran");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mocks.put).toHaveBeenCalledWith({ bot_name: "Pat at Luran" });
    expect(await screen.findByText("Note taker name saved")).toBeTruthy();
  });

  it("Clear sends null", async () => {
    mocks.put.mockResolvedValue(PREFS);
    render(<SettingsWorkspaceBotName initial={{ ...PREFS, bot_name: "Pat at Luran" }} workspaceName="Luran" />);
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(mocks.put).toHaveBeenCalledWith({ bot_name: null });
    expect(field().value).toBe("");
  });

  it("shows the server's plain refusal", async () => {
    mocks.put.mockRejectedValue(new ApiError(422, "bot_name_invalid", "Don't put a web address in the name."));
    render(<SettingsWorkspaceBotName initial={PREFS} workspaceName="Luran" />);
    await userEvent.type(field(), "x.com");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Don't put a web address in the name.")).toBeTruthy();
  });
});
