// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsLanguage } from "@/components/app/settings-language";
import { ApiError, NETWORK_MESSAGE } from "@/lib/api/client";
import { translateCapture } from "./helpers";

// The live "Default notes language" card (the designer's, drawn switched off until W7): its words
// and options as designed, saved on change, the designer's toast, no "not available yet" line.

const mocks = vi.hoisted(() => ({ set: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { settings: { language: { set: mocks.set } } } }));

const select = () => screen.getByRole("combobox", { name: "Default notes language" }) as HTMLSelectElement;

beforeEach(() => {
  mocks.set.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe("the card", () => {
  it("has the designer's title, line and choices, starting on what the account has", () => {
    render(<SettingsLanguage initial="fr" />);
    expect(screen.getByRole("heading", { level: 2, name: "Default notes language" })).toBeTruthy();
    expect(
      screen.getByText("Notes and transcripts open in this language. You can switch back to the original on any meeting."),
    ).toBeTruthy();
    expect(Array.from(select().options).map((o) => [o.value, o.text])).toEqual([
      ["en", "Original (no translation)"],
      ["fr", "French"],
      ["es", "Spanish"],
    ]);
    expect(select().value).toBe("fr");
    expect(select().disabled).toBe(false);
  });

  it("no longer says it is not available yet", () => {
    render(<SettingsLanguage initial="en" />);
    expect(screen.queryByText(/not available yet/i)).toBeNull();
  });
});

describe("choosing a language", () => {
  it("saves it at once and answers with the designer's toast, which goes away", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const saved = translateCapture("18-language-set-fr.json").response.body;
    mocks.set.mockResolvedValue(saved);
    render(<SettingsLanguage initial="en" />);
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).selectOptions(select(), "fr");
    expect(mocks.set).toHaveBeenCalledWith("fr");
    expect(await screen.findByText("Default notes language updated")).toBeTruthy();
    expect(select().value).toBe("fr");
    await act(() => vi.advanceTimersByTimeAsync(2300));
    expect(screen.queryByText("Default notes language updated")).toBeNull();
  });

  it("puts the old choice back and says why when saving fails", async () => {
    mocks.set.mockImplementation(async () => {
      throw new ApiError(0, "network_error", NETWORK_MESSAGE);
    });
    render(<SettingsLanguage initial="en" />);
    await userEvent.selectOptions(select(), "es");
    expect(await screen.findByText(NETWORK_MESSAGE)).toBeTruthy();
    await waitFor(() => expect(select().value).toBe("en"));
    expect(screen.queryByText("Default notes language updated")).toBeNull();
  });
});
