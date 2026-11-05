// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingView } from "@/components/app/meeting-view";
import type { Meeting, Template } from "@/lib/api/types";
import { captured, workspaceBody } from "./helpers";

// What the meeting page says about the reading of its notes for the Decisions and Review screens
// (`extraction_status`), from the bodies captured in docs/fe-captures/ai-workspace-w1/ (section 2 of
// docs/fe-integration-guide-ai-workspace.md), and the transcript tab the Review screen links to.

const mocks = vi.hoisted(() => ({ get: vi.fn(), regenerate: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({
  api: { meetings: { get: mocks.get, regenerate: mocks.regenerate, retry: vi.fn(), cancel: vi.fn(), end: vi.fn() } },
}));

const templates: Template[] = [{ id: 1, name: "General", instructions: "g" }];
const pending = workspaceBody<Meeting>("49-meeting-extraction-attempt-1.json"); // pending
const done = workspaceBody<Meeting>("11-meeting-after.json");
const failed = workspaceBody<Meeting>("51-meeting-extraction-attempt-3.json");
const skipped = workspaceBody<Meeting>("54-meeting-skipped.json");
const show = (m: Meeting, initialTab?: "notes" | "transcript") =>
  render(<MeetingView initial={m} templates={templates} initialTab={initialTab} />);

beforeEach(() => {
  mocks.get.mockReset();
  mocks.regenerate.mockReset();
  vi.useRealTimers();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("extraction state on the meeting page", () => {
  it("says the notes are being read while the state is pending", () => {
    show(pending);
    expect(pending.extraction_status).toBe("pending");
    expect(screen.getByRole("heading", { level: 2, name: "Decisions and review" })).toBeTruthy();
    expect(screen.getByText(/Reading the notes for decisions and items to check\./).getAttribute("role")).toBe("status");
  });

  it("links to Decisions and Review when it is done", () => {
    show(done);
    expect(done.extraction_status).toBe("done");
    expect(screen.getByRole("link", { name: "View" }).getAttribute("href")).toBe("/decisions");
    expect(screen.getByRole("link", { name: "Review" }).getAttribute("href")).toBe("/review");
  });

  it("counts this meeting's decisions and items to check, in the designer's words", () => {
    const counted = { ...done, decisions_count: 4, review_open_count: 3 } as Meeting;
    const { unmount } = show(counted);
    expect(screen.getByText("4 decisions")).toBeTruthy();
    expect(screen.getByText("In the decisions log")).toBeTruthy();
    expect(screen.getByText("3 to check")).toBeTruthy();
    expect(screen.getByText("Words that may have been misheard")).toBeTruthy();
    unmount();
    show({ ...done, decisions_count: 1, review_open_count: 0 } as Meeting);
    expect(screen.getByText("1 decision")).toBeTruthy();
    expect(screen.getByText("Nothing to check")).toBeTruthy();
    expect(screen.getByText("All review items are checked")).toBeTruthy();
    cleanup();
    show({ ...done, decisions_count: 0 } as Meeting);
    expect(screen.getByText("No decisions")).toBeTruthy();
  });

  it("says only the hints are missing, and how to try again, when it failed", () => {
    show(failed);
    expect(failed.extraction_status).toBe("failed");
    expect(screen.getByText(/could not be added\. Rewrite the notes to try again\./)).toBeTruthy();
  });

  it("draws the counts for an older meeting that was skipped (it has no review items)", () => {
    show(skipped);
    expect(skipped.extraction_status).toBe("skipped");
    expect(screen.getByText("Nothing to check")).toBeTruthy();
    expect(screen.getByRole("link", { name: "View" }).getAttribute("href")).toBe("/decisions");
  });

  it("says nothing when the meeting has no extraction state (an owner-app capture)", () => {
    show(captured<Meeting>("47-meeting-detail-ready.json"));
    expect(screen.queryByText("Decisions and review")).toBeNull();
  });

  it("warns near Rewrite notes that removed decisions and checked items may come back", () => {
    show(done);
    expect(screen.getByText(/Decisions you removed and items you checked may come back if the wording changes\./)).toBeTruthy();
  });
});

describe("polling the state", () => {
  it("asks again every 5 seconds while pending, and shows the new state", async () => {
    vi.useFakeTimers();
    mocks.get.mockResolvedValue(done);
    show(pending);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(mocks.get).toHaveBeenCalledWith(pending.id);
    expect(screen.getByRole("link", { name: "View" })).toBeTruthy();
    // done: no more polling
    mocks.get.mockClear();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(mocks.get).not.toHaveBeenCalled();
  });

  it("stops after 2 minutes and says so, still pending", async () => {
    vi.useFakeTimers();
    mocks.get.mockResolvedValue(pending);
    show(pending);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000 * 24);
    });
    expect(mocks.get).toHaveBeenCalledTimes(24);
    expect(screen.getByText(/is taking longer than usual/)).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(mocks.get).toHaveBeenCalledTimes(24);
  });

  it("does not poll a meeting whose reading is done", async () => {
    vi.useFakeTimers();
    show(done);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(mocks.get).not.toHaveBeenCalled();
  });
});

describe("initial tab", () => {
  it("opens on the transcript when the Review screen asks for it", () => {
    show(done, "transcript");
    expect(screen.getByRole("tab", { name: "Transcript" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("button", { name: /Copy transcript/ })).toBeTruthy();
  });

  it("opens on the notes by default", async () => {
    show(done);
    expect(screen.getByRole("tab", { name: "Notes" }).getAttribute("aria-selected")).toBe("true");
    await userEvent.click(screen.getByRole("tab", { name: "Transcript" }));
    expect(screen.getByRole("button", { name: /Copy transcript/ })).toBeTruthy();
  });
});
