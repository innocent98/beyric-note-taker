// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsYourName } from "@/components/app/settings-your-name";
import { ApiError } from "@/lib/api/client";

// The live "Your name" card (lead-designed, no designer file): the owner's label and help text, save,
// the server's plain refusal, no Clear. The payload shape is the one captured from the running app
// (docs/fe-captures/bot-name/).

const mocks = vi.hoisted(() => ({ set: vi.fn(), refresh: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { settings: { name: { set: mocks.set } } } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: mocks.refresh }) }));

const field = () => screen.getByRole("textbox", { name: "Your name" }) as HTMLInputElement;

beforeEach(() => {
  mocks.set.mockReset();
  mocks.refresh.mockReset();
});
afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

describe("the card", () => {
  it("has the owner's title and help text", () => {
    render(<SettingsYourName initial={{ name: "Ada Example" }} />);
    expect(screen.getByRole("heading", { level: 2, name: "Your name" })).toBeTruthy();
    expect(
      screen.getByText(
        "This is the name shown to your teammates and in the message the note taker posts when it joins your meetings.",
      ),
    ).toBeTruthy();
    expect(field().value).toBe("Ada Example");
  });

  it("starts empty for an account with no name, Save off until something is typed, and has no Clear", () => {
    render(<SettingsYourName initial={{ name: null }} />);
    expect(field().value).toBe("");
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
  });
});

describe("saving", () => {
  it("sends the typed name, keeps what the server answers and refreshes the page", async () => {
    mocks.set.mockResolvedValue({ name: "Ada Example" });
    render(<SettingsYourName initial={{ name: null }} />);
    await userEvent.type(field(), "  Ada   Example ");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mocks.set).toHaveBeenCalledWith("  Ada   Example ");
    expect(field().value).toBe("Ada Example");
    expect(await screen.findByText("Name saved")).toBeTruthy();
    expect(mocks.refresh).toHaveBeenCalled();
  });

  it("shows the server's plain refusal under the field and keeps the typed text", async () => {
    mocks.set.mockImplementation(async () => {
      throw new ApiError(422, "name_invalid", "Enter your name.");
    });
    render(<SettingsYourName initial={{ name: "Ada Example" }} />);
    await userEvent.clear(field());
    await userEvent.type(field(), " ");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Enter your name.")).toBeTruthy();
    expect(mocks.refresh).not.toHaveBeenCalled();
  });
});
