// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingView } from "@/components/app/meeting-view";
import { TRANSLATION_POLL_MS } from "@/components/app/translation";
import { ApiError } from "@/lib/api/client";
import type { Meeting, NoteLanguage, Template, Translation } from "@/lib/api/types";
import { translateBody, translateCapture } from "./helpers";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

// The Language select, the two bars under it, the translated notes and transcript, the copy
// button and the Download note, fed the bodies the running API answered with
// (docs/fe-captures/translate/, the model faked: they hold the designer's own French and Spanish).

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  request: vi.fn(),
  getTranslation: vi.fn(),
  remove: vi.fn(),
  export: vi.fn(),
  regenerate: vi.fn(),
}));
vi.mock("@/lib/api/browser", () => ({
  api: {
    meetings: {
      get: mocks.get,
      export: mocks.export,
      regenerate: mocks.regenerate,
      translations: { request: mocks.request, get: mocks.getTranslation, remove: mocks.remove },
    },
  },
}));

const templates: Template[] = [{ id: 1, name: "General", instructions: "g" }];
const before = () => translateBody<Meeting>("05-meeting-before.json");
const queued = () => translateBody<Translation>("07-translate-fr-queued.json");
const french = () => translateBody<Translation>("10-translate-fr-ready.json");
const stale = () => translateBody<Translation>("36-translate-fr-stale.json");
const failed = () => translateBody<Translation>("42-translate-es-failed.json");
const spanish = () => translateBody<Translation>("14-translate-es-ready.json");
const show = (m: Meeting = before(), defaultLanguage: NoteLanguage = "en") =>
  render(<MeetingView initial={m} templates={templates} defaultLanguage={defaultLanguage} />);
const select = () => screen.getByRole("combobox", { name: "Language" }) as HTMLSelectElement;
const withTranslation = (language: "fr" | "es", status: "ready" | "translating" = "ready"): Meeting => ({
  ...before(),
  translations: [{ language, status, stale: false }],
});

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
});
afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe("the select", () => {
  it("offers English (original), French and Spanish on the toolbar, on the original", () => {
    show();
    expect(Array.from(select().options).map((o) => o.text)).toEqual(["English (original)", "French", "Spanish"]);
    expect(select().value).toBe("en");
    expect(screen.queryByText(/Translat/)).toBeNull();
    expect(screen.getByRole("heading", { level: 2, name: "Summary" })).toBeTruthy();
  });

  it("is not on a meeting that is not ready", () => {
    show({ ...before(), status: "processing", notes: null, notes_html: null });
    expect(screen.queryByRole("combobox", { name: "Language" })).toBeNull();
  });
});

describe("asking for French", () => {
  it("says it is translating, asks every 2 seconds, then shows the translation and its bar", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocks.request.mockResolvedValue(queued());
    mocks.getTranslation.mockResolvedValueOnce(queued()).mockResolvedValue(french());
    show();
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).selectOptions(select(), "fr");
    expect(mocks.request).toHaveBeenCalledWith(before().id, "fr");
    expect(await screen.findByText("Translating to French...")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Summary" })).toBeTruthy(); // still the original
    await act(() => vi.advanceTimersByTimeAsync(TRANSLATION_POLL_MS));
    expect(await screen.findByText("Translating to French...")).toBeTruthy(); // the first answer: still working
    await act(() => vi.advanceTimersByTimeAsync(TRANSLATION_POLL_MS));
    expect(await screen.findByText("Translated from English to French")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Show original" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Résumé" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 2, name: "Summary" })).toBeNull();
    expect(select().value).toBe("fr");
    expect(screen.queryByText("Translating to French...")).toBeNull();
    expect(mocks.getTranslation).toHaveBeenCalledWith(before().id, "fr");
  });

  it("shows a cached translation at once, with no waiting line", async () => {
    mocks.request.mockResolvedValue(french());
    show();
    await userEvent.selectOptions(select(), "fr");
    expect(await screen.findByText("Translated from English to French")).toBeTruthy();
    expect(screen.queryByText("Translating to French...")).toBeNull();
    expect(mocks.getTranslation).not.toHaveBeenCalled();
  });

  it("draws the translated parts the way the original's are (check marks, the grey box)", async () => {
    mocks.request.mockResolvedValue(french());
    show();
    await userEvent.selectOptions(select(), "fr");
    await screen.findByText("Translated from English to French");
    const classes = Array.from(document.querySelectorAll(".notes-sec")).map((el) => el.className);
    expect(classes.some((c) => c.includes("notes-sec-decisions"))).toBe(true);
    expect(classes.some((c) => c.includes("notes-sec-check"))).toBe(true);
    expect(classes.some((c) => c.includes("notes-sec-questions"))).toBe(true);
  });

  it("goes back to the original with Show original, and to English from the select", async () => {
    mocks.request.mockResolvedValue(french());
    show();
    await userEvent.selectOptions(select(), "fr");
    await userEvent.click(await screen.findByRole("button", { name: "Show original" }));
    expect(screen.getByRole("heading", { level: 2, name: "Summary" })).toBeTruthy();
    expect(select().value).toBe("en");
    expect(screen.queryByText("Translated from English to French")).toBeNull();
    await userEvent.selectOptions(select(), "fr"); // held: no second request
    expect(mocks.request).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Translated from English to French")).toBeTruthy();
    await userEvent.selectOptions(select(), "en");
    expect(screen.getByRole("heading", { level: 2, name: "Summary" })).toBeTruthy();
  });

  it("shows the server's own words when asking is refused, and keeps what was on screen", async () => {
    const limit = translateCapture("48-translate-limit.json").response;
    mocks.request.mockRejectedValue(new ApiError(limit.status, limit.body.error.code, limit.body.error.message));
    show();
    await userEvent.selectOptions(select(), "fr");
    expect(await screen.findByText("You have used today's 20 translations. Try again tomorrow.")).toBeTruthy();
    expect(select().value).toBe("en");
    expect(screen.getByRole("heading", { level: 2, name: "Summary" })).toBeTruthy();
  });

  it("says a failed translation failed, in the server's sentence, and Try again asks again", async () => {
    mocks.request.mockResolvedValueOnce(failed()).mockResolvedValueOnce(spanish());
    show();
    await userEvent.selectOptions(select(), "es");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(
      "The translation did not come out right, so it was not kept. You can try again.",
    );
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Translated from English to Spanish")).toBeTruthy();
    expect(mocks.request).toHaveBeenCalledTimes(2);
  });
});

describe("the transcript tab and copying", () => {
  it("shows the translated transcript with the same speakers, and copies the shown words", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
    mocks.request.mockResolvedValue(french());
    show();
    await userEvent.selectOptions(select(), "fr");
    await screen.findByText("Translated from English to French");
    await userEvent.click(screen.getByRole("button", { name: /Copy notes/ }));
    expect(writeText).toHaveBeenLastCalledWith(french().notes);
    await userEvent.click(screen.getByRole("tab", { name: "Transcript" }));
    expect(screen.getByText("Alors, je vais juste vous présenter l'application.")).toBeTruthy();
    expect(screen.queryByText("So, I'm just going to introduce the app to you.")).toBeNull();
    expect(screen.getAllByText("Adebayo").length).toBeGreaterThan(0); // the names are not translated
    expect(screen.getByText("Translated from English to French")).toBeTruthy(); // the bar stays
    await userEvent.click(screen.getByRole("button", { name: /Copy transcript/ }));
    expect(writeText).toHaveBeenLastCalledWith(french().transcript);
  });

  it("copies the original again after Show original", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
    mocks.request.mockResolvedValue(french());
    show();
    await userEvent.selectOptions(select(), "fr");
    await userEvent.click(await screen.findByRole("button", { name: "Show original" }));
    await userEvent.click(screen.getByRole("button", { name: /Copy notes/ }));
    expect(writeText).toHaveBeenLastCalledWith(before().notes);
  });
});

describe("the default language", () => {
  it("opens a finished translation of it", async () => {
    mocks.getTranslation.mockResolvedValue(french());
    show(withTranslation("fr"), "fr");
    expect(await screen.findByText("Translated from English to French")).toBeTruthy();
    expect(select().value).toBe("fr");
    expect(mocks.getTranslation).toHaveBeenCalledWith(before().id, "fr");
    expect(mocks.request).not.toHaveBeenCalled(); // nothing is asked for on its own
  });

  it("opens in the original with a one-line offer when there is no translation, and asks nothing", async () => {
    show(before(), "fr");
    expect(screen.getByText("Your default notes language is French.")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Summary" })).toBeTruthy();
    expect(mocks.request).not.toHaveBeenCalled();
    expect(mocks.getTranslation).not.toHaveBeenCalled();
    mocks.request.mockResolvedValue(french());
    await userEvent.click(screen.getByRole("button", { name: "Translate to French" }));
    expect(mocks.request).toHaveBeenCalledWith(before().id, "fr");
    expect(await screen.findByText("Translated from English to French")).toBeTruthy();
  });

  it("stops offering once the owner chooses the original", async () => {
    mocks.getTranslation.mockResolvedValue(french());
    show(withTranslation("fr"), "fr");
    await userEvent.click(await screen.findByRole("button", { name: "Show original" }));
    expect(screen.queryByText("Your default notes language is French.")).toBeNull();
  });

  it("offers nothing when the default is the original", () => {
    show(before(), "en");
    expect(screen.queryByRole("button", { name: /Translate to/ })).toBeNull();
  });

  it("keeps asking while the default language's translation is still being made", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocks.getTranslation.mockResolvedValueOnce(queued()).mockResolvedValue(french());
    show(withTranslation("fr", "translating"), "fr");
    expect(await screen.findByText("Translating to French...")).toBeTruthy();
    await act(() => vi.advanceTimersByTimeAsync(TRANSLATION_POLL_MS));
    expect(await screen.findByText("Translated from English to French")).toBeTruthy();
  });
});

describe("a translation that is out of date", () => {
  it("is still shown, says the notes were rewritten, and Translate again asks for a new one", async () => {
    mocks.request.mockResolvedValueOnce(stale()).mockResolvedValueOnce(french());
    show();
    await userEvent.selectOptions(select(), "fr");
    expect(await screen.findByText("The notes were rewritten after this was translated.")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Résumé" })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Translate again" }));
    await waitFor(() => expect(mocks.request).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText(/were rewritten/)).toBeNull());
  });
});

describe("the Download menu", () => {
  it("says which language the file will be in, and asks for the translation that is on screen", async () => {
    mocks.request.mockResolvedValue(french());
    mocks.export.mockResolvedValue({ blob: new Blob(["x"]), filename: "n.docx" });
    vi.stubGlobal("URL", { ...URL, createObjectURL: () => "blob:x", revokeObjectURL: () => {} });
    show();
    await userEvent.click(screen.getByText("Download"));
    expect(screen.getByText("In English, with summary, decisions, action items and owners.")).toBeTruthy();
    await userEvent.selectOptions(select(), "fr");
    await screen.findByText("Translated from English to French");
    expect(screen.getByText("In French, with summary, decisions, action items and owners.")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /Word document/ }));
    expect(mocks.export).toHaveBeenCalledWith(before().id, "docx", false, "fr");
    vi.unstubAllGlobals();
  });
});
