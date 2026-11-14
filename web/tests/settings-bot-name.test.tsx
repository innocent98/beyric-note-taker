// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsBotName } from "@/components/app/settings-bot-name";
import { ApiError } from "@/lib/api/client";

// The live "Note taker name" card (lead-designed, no designer file): the approved label and help
// text with the server default read from the payload, save, clear and the server's plain refusal.
// The payload shape is the one captured from the running app (docs/fe-captures/bot-name/).

const mocks = vi.hoisted(() => ({ set: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { settings: { botName: { set: mocks.set } } } }));

const DEFAULT = "Beyric Meeting Support";
const field = () => screen.getByRole("textbox", { name: "Note taker name" }) as HTMLInputElement;

beforeEach(() => mocks.set.mockReset());
afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

describe("the card", () => {
  it("has the approved title and help text, naming the server default from the payload", () => {
    render(<SettingsBotName initial={{ bot_name: null, bot_name_default: DEFAULT }} />);
    expect(screen.getByRole("heading", { level: 2, name: "Note taker name" })).toBeTruthy();
    expect(
      screen.getByText(
        `This is the name people see when the note taker joins your meetings. Leave it empty to use “${DEFAULT}”. Choose a name your attendees will recognise as your notes taker, such as “Alex from Product”. Don't use another person's name, and don't hide that it records. Google Meet and Zoom may refuse bots whose names look like recorders.`,
      ),
    ).toBeTruthy();
    expect(field().placeholder).toBe(DEFAULT);
    expect(field().value).toBe("");
  });

  it("shows another server default, never a hard-coded one", () => {
    render(<SettingsBotName initial={{ bot_name: null, bot_name_default: "Acme Notes" }} />);
    expect(field().placeholder).toBe("Acme Notes");
    expect(screen.getByText(/Leave it empty to use “Acme Notes”/)).toBeTruthy();
  });

  it("starts with the saved name, Save off until it changes, Clear on", () => {
    render(<SettingsBotName initial={{ bot_name: "Alex from Product", bot_name_default: DEFAULT }} />);
    expect(field().value).toBe("Alex from Product");
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Clear" }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("saving and clearing", () => {
  it("sends the typed name and keeps what the server answers", async () => {
    mocks.set.mockResolvedValue({ bot_name: "Sam from Sales", bot_name_default: DEFAULT });
    render(<SettingsBotName initial={{ bot_name: null, bot_name_default: DEFAULT }} />);
    await userEvent.type(field(), "  Sam   from Sales ");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mocks.set).toHaveBeenCalledWith("  Sam   from Sales ");
    expect(field().value).toBe("Sam from Sales");
    expect(await screen.findByText("Note taker name saved")).toBeTruthy();
  });

  it("Clear sends null and empties the field", async () => {
    mocks.set.mockResolvedValue({ bot_name: null, bot_name_default: DEFAULT });
    render(<SettingsBotName initial={{ bot_name: "Alex from Product", bot_name_default: DEFAULT }} />);
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(mocks.set).toHaveBeenCalledWith(null);
    expect(field().value).toBe("");
    expect(await screen.findByText("Note taker name cleared")).toBeTruthy();
  });

  it("shows the server's plain refusal under the field and keeps the typed text", async () => {
    mocks.set.mockImplementation(async () => {
      throw new ApiError(422, "bot_name_invalid", "Don't put an email address in the name.");
    });
    render(<SettingsBotName initial={{ bot_name: null, bot_name_default: DEFAULT }} />);
    await userEvent.type(field(), "alex@acme.com");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Don't put an email address in the name.")).toBeTruthy();
  });
});
