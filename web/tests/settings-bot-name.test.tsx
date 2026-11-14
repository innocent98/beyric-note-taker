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
const PAYLOAD = {
  bot_name: null,
  bot_name_default: DEFAULT,
  warning: null,
  bot_name_applies_when_signed_in: false,
  meet_account_name: "Beyric Note Taker",
};
const MEET_SENTENCE = "On Google Meet the note taker always shows its own name, “Beyric Note Taker”. Your name shows on Zoom and Teams.";
const field = () => screen.getByRole("textbox", { name: "Note taker name" }) as HTMLInputElement;

beforeEach(() => mocks.set.mockReset());
afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

describe("the card", () => {
  it("has the approved title and help text, naming the server default from the payload", () => {
    render(<SettingsBotName initial={PAYLOAD} />);
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
    render(<SettingsBotName initial={{ ...PAYLOAD, bot_name_default: "Acme Notes" }} />);
    expect(field().placeholder).toBe("Acme Notes");
    expect(screen.getByText(/Leave it empty to use “Acme Notes”/)).toBeTruthy();
  });

  it("starts with the saved name, Save off until it changes, Clear on", () => {
    render(<SettingsBotName initial={{ ...PAYLOAD, bot_name: "Alex from Product" }} />);
    expect(field().value).toBe("Alex from Product");
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Clear" }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("saving and clearing", () => {
  it("sends the typed name and keeps what the server answers", async () => {
    mocks.set.mockResolvedValue({ ...PAYLOAD, bot_name: "Sam from Sales" });
    render(<SettingsBotName initial={PAYLOAD} />);
    await userEvent.type(field(), "  Sam   from Sales ");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mocks.set).toHaveBeenCalledWith("  Sam   from Sales ");
    expect(field().value).toBe("Sam from Sales");
    expect(await screen.findByText("Note taker name saved")).toBeTruthy();
  });

  it("Clear sends null and empties the field", async () => {
    mocks.set.mockResolvedValue(PAYLOAD);
    render(<SettingsBotName initial={{ ...PAYLOAD, bot_name: "Alex from Product" }} />);
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(mocks.set).toHaveBeenCalledWith(null);
    expect(field().value).toBe("");
    expect(await screen.findByText("Note taker name cleared")).toBeTruthy();
  });

  it("shows the server's plain refusal under the field and keeps the typed text", async () => {
    mocks.set.mockImplementation(async () => {
      throw new ApiError(422, "bot_name_invalid", "Don't put an email address in the name.");
    });
    render(<SettingsBotName initial={PAYLOAD} />);
    await userEvent.type(field(), "alex@acme.com");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Don't put an email address in the name.")).toBeTruthy();
  });
});

describe("the lines the lead added", () => {
  it("says the Google Meet bot always shows the account's own name, from the server", () => {
    render(<SettingsBotName initial={PAYLOAD} />);
    expect(screen.getByText(MEET_SENTENCE)).toBeTruthy();
    cleanup();
    render(<SettingsBotName initial={{ ...PAYLOAD, meet_account_name: "Acme Notes" }} />);
    expect(screen.getByText(/always shows its own name, “Acme Notes”\. Your name shows on Zoom and Teams\./)).toBeTruthy();
  });

  it("says nothing about Meet's name when the server gives none", () => {
    render(<SettingsBotName initial={{ ...PAYLOAD, meet_account_name: null }} />);
    expect(screen.queryByText(/On Google Meet/)).toBeNull();
    expect(screen.queryByText(/Zoom and Teams/)).toBeNull();
  });

  it("shows the soft warning for a saved recorder-like name, and a save still works", async () => {
    const warning = "Google Meet and Zoom may refuse bots with names like this.";
    render(<SettingsBotName initial={{ ...PAYLOAD, bot_name: "Alex AI Notetaker", warning }} />);
    expect(screen.getByText(warning)).toBeTruthy();
    mocks.set.mockResolvedValue({ ...PAYLOAD, bot_name: "Alex from Product" });
    await userEvent.clear(field());
    await userEvent.type(field(), "Alex from Product");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Note taker name saved")).toBeTruthy();
    expect(screen.queryByText(warning)).toBeNull();
  });

  it("shows the warning that comes back from a save", async () => {
    const warning = "Google Meet and Zoom may refuse bots with names like this.";
    mocks.set.mockResolvedValue({ ...PAYLOAD, bot_name: "Meeting Bot", warning });
    render(<SettingsBotName initial={PAYLOAD} />);
    await userEvent.type(field(), "Meeting Bot");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(warning)).toBeTruthy();
    expect(field().value).toBe("Meeting Bot");
  });
});
