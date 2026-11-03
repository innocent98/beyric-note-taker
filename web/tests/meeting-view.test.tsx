// @vitest-environment jsdom
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingView } from "@/components/app/meeting-view";
import { ApiError } from "@/lib/api/client";
import type { Meeting, Template } from "@/lib/api/types";
import { captured } from "./helpers";

// The screen is fed the exact bodies stream B captured from the running API, one per state of the
// state machine in docs/fe-integration-guide-owner-app.md section 7.

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  retry: vi.fn(),
  cancel: vi.fn(),
  regenerate: vi.fn(),
}));
vi.mock("@/lib/api/browser", () => ({
  api: { meetings: { get: mocks.get, retry: mocks.retry, cancel: mocks.cancel, regenerate: mocks.regenerate } },
}));

const templates: Template[] = [
  { id: 1, name: "General", instructions: "g" },
  { id: 2, name: "Client call", instructions: "c" },
];
const meeting = (file: string) => captured<Meeting>(file);
const show = (m: Meeting) => render(<MeetingView initial={m} templates={templates} />);

beforeEach(() => {
  vi.useRealTimers();
  Object.values(mocks).forEach((fn) => fn.mockReset());
});

describe("ready meeting", () => {
  it("shows the server-rendered notes, the template and a copy button", () => {
    show(meeting("47-meeting-detail-ready.json"));
    expect(screen.getByRole("heading", { level: 1, name: "Google Meet abc-defg-hij" })).toBeTruthy();
    expect(screen.getByText("Ready")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Summary" })).toBeTruthy();
    expect(document.querySelector(".notes-body strong")?.textContent).toBe("beta");
    expect(screen.getByRole("button", { name: /Copy notes/ })).toBeTruthy();
    expect(document.querySelector(".details")?.textContent).toContain("General"); // from template_id
    expect(screen.queryByText(/updates every 30 seconds/)).toBeNull();
  });

  it("switches to the transcript tab, with the speakers and the incomplete-speakers notice", async () => {
    show(meeting("47-meeting-detail-ready.json"));
    await userEvent.click(screen.getByRole("tab", { name: "Transcript" }));
    expect(screen.getByText("Ada Example")).toBeTruthy();
    expect(screen.getByText("Ben Sample")).toBeTruthy();
    expect(screen.getByText("Speaker names unavailable for part of this transcript.")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Copy transcript/ })).toBeTruthy();
  });

  it("copies the raw markdown notes, not the HTML", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
    const m = meeting("47-meeting-detail-ready.json");
    show(m);
    await userEvent.click(screen.getByRole("button", { name: /Copy notes/ }));
    expect(writeText).toHaveBeenCalledWith(m.notes);
    expect(await screen.findByText("Copied")).toBeTruthy();
  });

  it("tells the owner the note taker left early", () => {
    show(meeting("48-meeting-detail-ready-removed.json"));
    expect(screen.getByText("The note taker left the meeting early or was removed. The notes may be incomplete.")).toBeTruthy();
  });

  it("rewrites with the chosen template, locks the form while it runs, then shows the result", async () => {
    let finish: (m: Meeting) => void = () => undefined;
    mocks.regenerate.mockReturnValue(new Promise<Meeting>((resolve) => (finish = resolve)));
    const m = meeting("47-meeting-detail-ready.json");
    show(m);
    await userEvent.selectOptions(screen.getByLabelText("Rewrite the notes with a template"), "2");
    await userEvent.click(screen.getByRole("button", { name: "Rewrite notes" }));
    expect(mocks.regenerate).toHaveBeenCalledWith(7, 2);
    const busy = screen.getByRole("button", { name: "Rewriting..." }) as HTMLButtonElement;
    expect(busy.disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toMatch(/can take a few minutes/);
    await act(async () => finish({ ...m, template_id: 2, notes_html: "<h2>New</h2>" }));
    expect(screen.getByRole("heading", { level: 2, name: "New" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Rewrite notes" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("keeps the old notes and shows the API's message when a rewrite fails", async () => {
    mocks.regenerate.mockRejectedValue(new ApiError(502, "notes_failed", "The notes could not be written. The old notes were kept."));
    show(meeting("47-meeting-detail-ready.json"));
    await userEvent.click(screen.getByRole("button", { name: "Rewrite notes" }));
    expect(await screen.findByText("The notes could not be written. The old notes were kept.")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Summary" })).toBeTruthy();
  });

  it("says a gateway timeout is not a failure and checks again later", async () => {
    mocks.regenerate.mockRejectedValue(new ApiError(504, "gateway_error", "The server did not answer in time. Check again in a minute."));
    show(meeting("47-meeting-detail-ready.json"));
    await userEvent.click(screen.getByRole("button", { name: "Rewrite notes" }));
    expect(await screen.findByText(/taking longer than expected/)).toBeTruthy();
  });
});

describe("waiting and working", () => {
  it("shows the waiting state with a cancel button and the 30-second line", () => {
    show(meeting("35-meeting-detail-bot-sent.json"));
    expect(screen.getByRole("heading", { level: 2, name: "Bot sent" })).toBeTruthy();
    expect(screen.getByText("This page updates every 30 seconds.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel and remove the bot" })).toBeTruthy();
  });

  it.each([
    ["105-meeting-detail-bot-joining.json", "Bot is joining the meeting"],
    ["106-meeting-detail-bot-awaiting-admission.json", "Waiting to be let in. Admit 'Beyric Note Taker (recording)' in the meeting"],
    ["107-meeting-detail-bot-in-meeting.json", "Bot is in the meeting"],
    ["108-meeting-detail-bot-recording.json", "Recording"],
    ["109-meeting-detail-bot-needs-help.json", /waiting on a prompt in the meeting.*Check the meeting for a prompt\./],
    ["104-meeting-detail-bot-state-unknown.json", "Bot sent"],
  ])("says what the bot is doing (%s), in the badge and in the wait card", (file, words) => {
    show(meeting(file));
    expect(screen.getAllByText(words)).toHaveLength(2); // the badge and the card heading
    expect(screen.getByRole("heading", { level: 2, name: words })).toBeTruthy();
    expect(screen.queryByText("Waiting for the note taker to join")).toBeNull();
    expect(screen.getByText("This page updates every 30 seconds.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel and remove the bot" })).toBeTruthy();
  });

  it("does not claim a recording before one exists", () => {
    show(meeting("107-meeting-detail-bot-in-meeting.json"));
    expect(document.querySelector(".detail-title")?.textContent).not.toMatch(/record/i); // the badge
    expect(screen.getByRole("heading", { level: 2 }).textContent).not.toMatch(/record/i);
    expect(document.querySelector(".state-title + p")?.textContent).not.toMatch(/record/i);
  });

  it("follows the bot from joining to recording without a reload", async () => {
    vi.useFakeTimers();
    mocks.get
      .mockResolvedValueOnce(meeting("106-meeting-detail-bot-awaiting-admission.json"))
      .mockResolvedValueOnce(meeting("107-meeting-detail-bot-in-meeting.json"))
      .mockResolvedValue(meeting("108-meeting-detail-bot-recording.json"));
    show(meeting("105-meeting-detail-bot-joining.json"));
    expect(screen.getAllByText("Bot is joining the meeting").length).toBeGreaterThan(0);
    await act(async () => vi.advanceTimersByTime(30_500));
    expect(screen.getAllByText(/Waiting to be let in\. Admit/).length).toBeGreaterThan(0);
    await act(async () => vi.advanceTimersByTime(30_000));
    expect(screen.getAllByText("Bot is in the meeting").length).toBeGreaterThan(0);
    await act(async () => vi.advanceTimersByTime(30_000));
    expect(screen.getAllByText("Recording").length).toBeGreaterThan(0);
    expect(screen.queryByText("Bot is in the meeting")).toBeNull();
  });

  it("shows the attempt notice on a second Zoom attempt", () => {
    show(meeting("41-meeting-detail-zoom-second-attempt.json"));
    expect(screen.getByText(/Zoom turned the bot away, so a new one was sent \(attempt 2 of 3\)\./)).toBeTruthy();
  });

  it("says when the second bot goes after Google refused the first, with the same wait page", () => {
    show(meeting("103-meeting-detail-refused-paused-before-retry.json"));
    expect(screen.getByText(/Google would not let the bot ask to join, so it was taken out/)).toBeTruthy();
    expect(screen.getByText(/A new bot will be sent at about/)).toBeTruthy();
    expect(screen.getByText(/UTC \(attempt 2 of 2\)/)).toBeTruthy();
  });

  it("counts the wait down on a refused meeting by asking the server again", async () => {
    vi.useFakeTimers();
    mocks.get
      .mockResolvedValueOnce(meeting("99-meeting-detail-refused-waiting.json"))
      .mockResolvedValue(meeting("101-meeting-detail-refused-can-send-again.json"));
    show(meeting("99-meeting-detail-refused-waiting.json"));
    expect(screen.getByText(/you can send it again in 4 minutes/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    await act(async () => vi.advanceTimersByTime(30_500));
    expect(mocks.get).toHaveBeenCalledTimes(1); // refresh_after_seconds is 30 while the wait runs
    await act(async () => vi.advanceTimersByTime(30_000));
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("shows the step in plain words while processing, with no cancel", () => {
    show(meeting("44-meeting-detail-processing-transcribing.json"));
    expect(screen.getAllByText("Writing the transcript").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Cancel and remove the bot" })).toBeNull();
  });

  it("polls every 30 seconds while working, and stops once the meeting is ready", async () => {
    vi.useFakeTimers();
    mocks.get.mockResolvedValueOnce(meeting("46-meeting-detail-processing-notes.json")).mockResolvedValue(meeting("47-meeting-detail-ready.json"));
    show(meeting("43-meeting-detail-processing.json"));
    await act(async () => vi.advanceTimersByTime(29_000));
    expect(mocks.get).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTime(1_500));
    expect(mocks.get).toHaveBeenCalledTimes(1);
    expect(mocks.get).toHaveBeenCalledWith(meeting("43-meeting-detail-processing.json").id);
    await act(async () => vi.advanceTimersByTime(30_000));
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("heading", { level: 2, name: "Summary" })).toBeTruthy();
    await act(async () => vi.advanceTimersByTime(120_000));
    expect(mocks.get).toHaveBeenCalledTimes(2); // refresh_after_seconds is null now
  });

  it("does not poll a hidden tab", async () => {
    vi.useFakeTimers();
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    show(meeting("43-meeting-detail-processing.json"));
    await act(async () => vi.advanceTimersByTime(90_000));
    expect(mocks.get).not.toHaveBeenCalled();
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  });

  it("cancels only after the confirmation, and shows the failed state it returns", async () => {
    mocks.cancel.mockResolvedValue(meeting("52-meeting-detail-failed-never-recorded.json"));
    show(meeting("35-meeting-detail-bot-sent.json"));
    fireEvent.click(screen.getByRole("button", { name: "Cancel and remove the bot" }));
    expect(mocks.cancel).not.toHaveBeenCalled();
    const confirm = screen.getAllByRole("button", { name: "Cancel and remove the bot", hidden: true }).pop()!;
    await userEvent.click(confirm);
    expect(mocks.cancel).toHaveBeenCalledWith(1);
    expect(await screen.findByText("What went wrong")).toBeTruthy();
  });
});

describe("failed meeting", () => {
  it("offers Try again when it can be retried, and shows the processing state after", async () => {
    mocks.retry.mockResolvedValue(meeting("43-meeting-detail-processing.json"));
    const m = meeting("49-meeting-detail-failed-retryable.json");
    show(m);
    expect(screen.getByText("What went wrong")).toBeTruthy();
    expect(screen.getByText(m.failure_reason!)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(mocks.retry).toHaveBeenCalledWith(m.id);
    expect(await screen.findByText("This page updates every 30 seconds.")).toBeTruthy();
  });

  it.each([
    ["50-meeting-detail-failed-no-speech.json", "This meeting can't be retried: no speech was found, so trying again would give the same result."],
    ["51-meeting-detail-failed-audio-deleted.json", "This meeting can't be retried: the audio has been deleted and no transcript was saved."],
    ["52-meeting-detail-failed-never-recorded.json", "This meeting can't be retried: nothing was captured. Send the bot again with a new link."],
  ])("says why %s cannot be retried, with no button", (file, message) => {
    show(meeting(file));
    expect(screen.getByText(message)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("refetches after a 409 so the screen follows the server", async () => {
    mocks.retry.mockRejectedValue(new ApiError(409, "not_retryable", "This meeting cannot be retried."));
    mocks.get.mockResolvedValue(meeting("50-meeting-detail-failed-no-speech.json"));
    show(meeting("49-meeting-detail-failed-retryable.json"));
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("This meeting cannot be retried.")).toBeTruthy();
    await waitFor(() => expect(mocks.get).toHaveBeenCalled());
  });
});
