// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingView } from "@/components/app/meeting-view";
import { ApiError } from "@/lib/api/client";
import type { Meeting, Template } from "@/lib/api/types";
import { REPO, read } from "./helpers";

// The page is fed the exact bodies captured from the running API
// (docs/fe-captures/transcript-edit, `scripts/capture_transcript_edit_api.py`).

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  regenerate: vi.fn(),
  editTranscript: vi.fn(),
  restoreTranscript: vi.fn(),
  push: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push, refresh: vi.fn() }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    milestones: { forMeeting: () => Promise.resolve({ milestones: [] }), confirm: vi.fn() },
    series: { list: vi.fn().mockResolvedValue({ timezone: "UTC", series: [] }), create: vi.fn() },
    meetings: {
      get: mocks.get,
      regenerate: mocks.regenerate,
      editTranscript: mocks.editTranscript,
      restoreTranscript: mocks.restoreTranscript,
    },
  },
}));

const templates: Template[] = [{ id: 1, name: "General", instructions: "g" }];
const capture = (file: string) =>
  JSON.parse(read(join(REPO, "docs/fe-captures/transcript-edit", file))).response.body as Meeting;
const before = () => capture("04-detail-before.json"); // owner, nothing edited
const edited = () => capture("05-rename.json"); // owner, after renaming Speaker 2
const reader = () => capture("24-teammate-detail.json"); // a teammate: can_edit_transcript false

async function openTranscript(m: Meeting) {
  render(<MeetingView initial={m} templates={templates} />);
  await userEvent.click(screen.getByRole("tab", { name: "Transcript" }));
}

beforeEach(() => Object.values(mocks).forEach((fn) => fn.mockReset()));
afterEach(cleanup);

describe("who sees the editor", () => {
  it("offers Edit transcript to someone who may edit, and nothing is editable until they ask", async () => {
    await openTranscript(before());
    expect(screen.queryByRole("button", { name: /Edit line/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Rename speaker/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Edit transcript" }));
    expect(screen.getAllByRole("button", { name: /Edit line/ })).toHaveLength(4);
    expect(screen.getByRole("button", { name: "Rename speaker Speaker 2" })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Done editing" }));
    expect(screen.queryByRole("button", { name: /Edit line/ })).toBeNull();
  });

  it("shows a reader no edit control, no restore and no rewrite button, only who edited", async () => {
    await openTranscript(reader());
    expect(screen.queryByRole("button", { name: /Edit transcript/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Restore original/ })).toBeNull();
    expect(screen.getByText(/Edited by owner/)).toBeTruthy();
    expect(
      screen.getByText("The transcript was edited after these notes were written, so they may not match it."),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Rewrite notes" })).toBeNull();
  });
});

describe("rename a speaker", () => {
  it("sends the rename with the page's version and shows the answer", async () => {
    mocks.editTranscript.mockResolvedValue(edited());
    await openTranscript(before());
    await userEvent.click(screen.getByRole("button", { name: "Edit transcript" }));
    await userEvent.click(screen.getByRole("button", { name: "Rename speaker Speaker 2" }));
    const box = screen.getByLabelText("New name for Speaker 2");
    await userEvent.clear(box);
    await userEvent.type(box, "Daniel Example");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    expect(mocks.editTranscript).toHaveBeenCalledWith(before().id, {
      version: before().transcript_version,
      rename: { from: "Speaker 2", to: "Daniel Example", merge: false },
    });
    await waitFor(() => expect(screen.queryByLabelText("New name for Speaker 2")).toBeNull());
    expect(screen.getByRole("button", { name: "Rename speaker Daniel Example" })).toBeTruthy();
  });

  it("asks before merging into a name that is already in the meeting", async () => {
    mocks.editTranscript.mockResolvedValue(edited());
    await openTranscript(before());
    await userEvent.click(screen.getByRole("button", { name: "Edit transcript" }));
    await userEvent.click(screen.getByRole("button", { name: "Rename speaker Speaker 2" }));
    const box = screen.getByLabelText("New name for Speaker 2");
    await userEvent.clear(box);
    await userEvent.type(box, "Ben Sample");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    expect(mocks.editTranscript).not.toHaveBeenCalled();
    expect(screen.getByText(/Ben Sample is already in this meeting\. Merge Speaker 2 into Ben Sample\?/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Merge into Ben Sample" }));
    expect(mocks.editTranscript).toHaveBeenCalledWith(before().id, {
      version: before().transcript_version,
      rename: { from: "Speaker 2", to: "Ben Sample", merge: true },
    });
  });
});

describe("the forms", () => {
  async function openRename() {
    await openTranscript(before());
    await userEvent.click(screen.getByRole("button", { name: "Edit transcript" }));
    await userEvent.click(screen.getByRole("button", { name: "Rename speaker Speaker 2" }));
    const box = screen.getByLabelText("New name for Speaker 2");
    await userEvent.clear(box);
    return box;
  }

  it("asks about the merge for the name the server will save, not the raw text", async () => {
    mocks.editTranscript.mockResolvedValue(edited());
    const box = await openRename();
    await userEvent.type(box, "  Ben Sample: ");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    expect(mocks.editTranscript).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toMatch(/Ben Sample is already in this meeting/);
    await userEvent.click(screen.getByRole("button", { name: "Merge into Ben Sample" }));
    expect(mocks.editTranscript).toHaveBeenCalledWith(before().id, {
      version: before().transcript_version,
      rename: { from: "Speaker 2", to: "Ben Sample", merge: true },
    });
  });

  it("closes on Escape and gives focus back to the Rename button", async () => {
    const box = await openRename();
    await userEvent.type(box, "Dan");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByLabelText("New name for Speaker 2")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Rename speaker Speaker 2" }));
  });

  it("gives focus back to the pencil after Cancel, Escape and a save", async () => {
    mocks.editTranscript.mockResolvedValue(edited());
    await openTranscript(before());
    await userEvent.click(screen.getByRole("button", { name: "Edit transcript" }));
    await userEvent.click(screen.getByRole("button", { name: "Edit line 2" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Edit line 2" }));
    await userEvent.click(screen.getByRole("button", { name: "Edit line 2" }));
    await userEvent.keyboard("{Escape}");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Edit line 2" }));
    await userEvent.click(screen.getByRole("button", { name: "Edit line 2" }));
    await userEvent.type(screen.getByLabelText("Words of line 2"), " Now.");
    await userEvent.click(screen.getByRole("button", { name: "Save line" }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Edit line 2" })));
  });

  it("shows a refusal inside the form that asked, announced, not above the speaker list", async () => {
    mocks.editTranscript.mockRejectedValue(new ApiError(422, "no_change", "Nothing changed on this line."));
    await openTranscript(before());
    await userEvent.click(screen.getByRole("button", { name: "Edit transcript" }));
    await userEvent.click(screen.getByRole("button", { name: "Edit line 3" }));
    await userEvent.type(screen.getByLabelText("Words of line 3"), " Really.");
    await userEvent.click(screen.getByRole("button", { name: "Save line" }));
    const form = screen.getByRole("form", { name: "Edit line 3" });
    const alert = await within(form).findByRole("alert");
    expect(alert.textContent).toContain("Nothing changed on this line.");
    expect(screen.getAllByRole("alert")).toHaveLength(1); // not repeated above the speaker list
    await userEvent.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("Nothing changed on this line.")).toBeNull(); // Cancel clears it
  });
});

describe("fix or move one line", () => {
  it("sends only what changed on the line", async () => {
    mocks.editTranscript.mockResolvedValue(edited());
    await openTranscript(before());
    await userEvent.click(screen.getByRole("button", { name: "Edit transcript" }));
    await userEvent.click(screen.getByRole("button", { name: "Edit line 2" }));
    const save = screen.getByRole("button", { name: "Save line" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true); // nothing changed yet
    const words = screen.getByLabelText("Words of line 2");
    await userEvent.clear(words);
    await userEvent.type(words, "I can ship the beta on Thursday.");
    await userEvent.click(save);
    expect(mocks.editTranscript).toHaveBeenCalledWith(before().id, {
      version: before().transcript_version,
      line: 2,
      text: "I can ship the beta on Thursday.",
    });
  });

  it("moves a line to another speaker, or to a name typed in", async () => {
    mocks.editTranscript.mockResolvedValue(edited());
    await openTranscript(before());
    await userEvent.click(screen.getByRole("button", { name: "Edit transcript" }));
    await userEvent.click(screen.getByRole("button", { name: "Edit line 2" }));
    await userEvent.selectOptions(screen.getByLabelText("Speaker of line 2"), "Ben Sample");
    await userEvent.click(screen.getByRole("button", { name: "Save line" }));
    expect(mocks.editTranscript).toHaveBeenLastCalledWith(before().id, {
      version: before().transcript_version,
      line: 2,
      speaker: "Ben Sample",
    });
    await userEvent.click(screen.getByRole("button", { name: "Edit line 4" }));
    await userEvent.selectOptions(screen.getByLabelText("Speaker of line 4"), "Someone else...");
    await userEvent.type(screen.getByLabelText("Name of the speaker"), "Cleo Example");
    await userEvent.click(screen.getByRole("button", { name: "Save line" }));
    expect(mocks.editTranscript).toHaveBeenLastCalledWith(
      before().id,
      expect.objectContaining({ line: 4, speaker: "Cleo Example" }),
    );
  });

  it("keeps the form open and says why when the edit is refused, and reads the meeting again on a 409", async () => {
    mocks.editTranscript.mockRejectedValue(
      new ApiError(409, "transcript_changed", "The transcript changed since you opened it. Reload the page and try again."),
    );
    mocks.get.mockResolvedValue(edited());
    await openTranscript(before());
    await userEvent.click(screen.getByRole("button", { name: "Edit transcript" }));
    await userEvent.click(screen.getByRole("button", { name: "Edit line 1" }));
    const words = screen.getByLabelText("Words of line 1");
    await userEvent.type(words, " Really.");
    await userEvent.click(screen.getByRole("button", { name: "Save line" }));
    expect(await screen.findByText(/The transcript changed since you opened it/)).toBeTruthy();
    await waitFor(() => expect(mocks.get).toHaveBeenCalledWith(before().id));
  });
});

describe("refusals are shown in the API's words", () => {
  it("shows the text of a 422 or 404, which the generic helper would hide", async () => {
    mocks.editTranscript.mockRejectedValueOnce(new ApiError(404, "not_found", "No such meeting"));
    await openTranscript(before());
    await userEvent.click(screen.getByRole("button", { name: "Edit transcript" }));
    await userEvent.click(screen.getByRole("button", { name: "Edit line 1" }));
    await userEvent.type(screen.getByLabelText("Words of line 1"), " More.");
    await userEvent.click(screen.getByRole("button", { name: "Save line" }));
    expect(await screen.findByText("This meeting is not available any more.")).toBeTruthy();
  });
});

describe("the notes are out of date", () => {
  it("says so, and the notice's button is the existing Rewrite action", async () => {
    mocks.regenerate.mockResolvedValue({ ...edited(), notes_outdated: false });
    render(<MeetingView initial={edited()} templates={templates} />);
    const notice = screen.getByText(/The transcript was edited after these notes were written\. Rewrite notes to update them\./);
    await userEvent.click(within(notice.closest(".alert") as HTMLElement).getByRole("button", { name: "Rewrite notes" }));
    expect(mocks.regenerate).toHaveBeenCalledWith(edited().id, 1);
    await waitFor(() => expect(screen.queryByText(/Rewrite notes to update them/)).toBeNull());
  });

  it("shows no notice for a meeting nobody edited", () => {
    render(<MeetingView initial={before()} templates={templates} />);
    expect(screen.queryByText(/edited after these notes were written/)).toBeNull();
  });
});

describe("restore the original", () => {
  it("asks first, then restores with the page's version", async () => {
    mocks.restoreTranscript.mockResolvedValue({ ...before(), notes_outdated: false });
    await openTranscript(edited());
    expect(screen.getByText(/Edited by owner/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Restore original" }));
    expect(mocks.restoreTranscript).not.toHaveBeenCalled();
    expect(screen.getByText(/Restore the original transcript\?/)).toBeTruthy();
    const ask = screen.getByRole("group", { name: "Restore the original transcript?" });
    await userEvent.click(within(ask).getByRole("button", { name: "Restore original" }));
    expect(mocks.restoreTranscript).toHaveBeenCalledWith(edited().id, edited().transcript_version);
    await waitFor(() => expect(screen.queryByText(/Edited by/)).toBeNull());
  });

  it("can be cancelled", async () => {
    await openTranscript(edited());
    await userEvent.click(screen.getByRole("button", { name: "Restore original" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText(/Restore the original transcript\?/)).toBeNull();
    expect(mocks.restoreTranscript).not.toHaveBeenCalled();
  });
});
