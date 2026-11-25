// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentBody } from "@/components/app/document-body";
import { DocumentView } from "@/components/app/document-view";
import { DOCUMENT_POLL_MS } from "@/components/app/use-document";
import { ApiError } from "@/lib/api/client";
import type { DocumentDraft } from "@/lib/api/types";
import { blocksToText } from "@/lib/documents";
import { recipesBody, recipesCapture } from "./helpers";

// The document screen: the draft as the API answers it (docs/fe-captures/recipes/, the model faked
// there; the proposal with tables is docs/fe-captures/recipes/exports/24-the-draft.json).

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  edit: vi.fn(),
  create: vi.fn(),
  remove: vi.fn(),
  export: vi.fn(),
  push: vi.fn(),
  save: vi.fn(),
  write: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/lib/api/browser", () => ({
  api: { documents: { get: mocks.get, edit: mocks.edit, create: mocks.create, remove: mocks.remove, export: mocks.export } },
}));
vi.mock("@/lib/save-file", () => ({ saveBlob: mocks.save }));

const writing = () => recipesBody<DocumentDraft>("09-get-writing.json");
const email = () => recipesBody<DocumentDraft>("11-get-ready.json");
const proposal = () => recipesBody<DocumentDraft>("exports/24-the-draft.json");
const failed = () => recipesBody<DocumentDraft>("25-get-failed.json");
const stale = () => recipesBody<DocumentDraft>("21-get-stale.json");
const orphan = () => recipesBody<DocumentDraft>("23-get-meeting-deleted.json");
const saved = () => recipesBody<DocumentDraft>("15-patch.json");
const show = (draft: DocumentDraft) => render(<DocumentView initial={draft} />);
const action = (name: string) => screen.getByRole("button", { name });

function clipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
  return writeText;
}

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
  clipboard();
});
afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

describe("a draft being written", () => {
  it("says so, asks every 2 seconds, and shows the draft when it is ready", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocks.get.mockResolvedValueOnce(writing()).mockResolvedValue(email());
    show(writing());
    expect(screen.getByRole("status").textContent).toMatch(/Writing your draft/);
    expect(screen.queryByRole("button", { name: "Copy" })).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DOCUMENT_POLL_MS);
    });
    expect(mocks.get).toHaveBeenCalledWith(writing().id);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DOCUMENT_POLL_MS);
    });
    await waitFor(() => expect(screen.getByText(/Hi Daniel, thank you for your time today\./)).toBeTruthy());
    expect(screen.getByRole("heading", { level: 1, name: "Thanks for today, next steps on the onboarding" })).toBeTruthy();
    const calls = mocks.get.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DOCUMENT_POLL_MS * 3);
    });
    expect(mocks.get.mock.calls.length).toBe(calls); // it stops asking once it is ready
  });

  it("can be discarded while it is still being written, but not regenerated or copied", async () => {
    mocks.remove.mockResolvedValue(undefined);
    show(writing());
    expect(screen.queryByRole("button", { name: "Regenerate" })).toBeNull();
    await userEvent.click(action("Discard"));
    await userEvent.click(action("Discard draft"));
    expect(mocks.remove).toHaveBeenCalledWith(writing().id);
  });

  it("keeps asking through a lost connection and says when the draft was discarded elsewhere", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocks.get.mockRejectedValueOnce(new TypeError("offline")).mockRejectedValue(new ApiError(404, "not_found", "No such document."));
    show(writing());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DOCUMENT_POLL_MS * 2);
    });
    await waitFor(() => expect(screen.getByText(/was discarded/)).toBeTruthy());
  });
});

describe("a draft that failed", () => {
  it("shows the plain message and offers Regenerate and Discard, not Copy", () => {
    show(failed());
    expect(screen.getByRole("alert").textContent).toContain("The writing service sent back something unusable. Try again.");
    expect(action("Regenerate")).toBeTruthy();
    expect(action("Discard")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Copy" })).toBeNull();
  });
});

describe("a ready draft", () => {
  it("links a source chip to its transcript line and says where each part came from", () => {
    show(email());
    const link = screen.getByRole("link", { name: "Line 2" }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/meetings/1?tab=transcript&line=2");
    expect(link.title).toContain("We would like the onboarding for 50,000 naira");
    expect(screen.getAllByText("Drafted from").length).toBe(3);
  });

  it("highlights the blank and the flagged figure and counts them", () => {
    const { container } = show(email());
    expect(container.querySelector("mark.doc-blank")?.textContent).toBe("[need: call date]");
    const check = container.querySelector("mark.doc-check");
    expect(check?.textContent).toContain("120,000 naira");
    expect(check?.getAttribute("title")).toBe("check this");
    expect(screen.getByText("1 blank to fill")).toBeTruthy();
    expect(screen.getByText("1 figure to check")).toBeTruthy();
  });

  it("shows no count when there is nothing to fill or check", () => {
    const clean = { ...saved(), checks: { blanks: [], flags: [] } } as DocumentDraft;
    show(clean);
    expect(screen.queryByText(/to fill/)).toBeNull();
    expect(screen.queryByText(/to check/)).toBeNull();
  });

  it("draws a table with its header row", () => {
    show(proposal());
    const tables = screen.getAllByRole("table");
    expect(tables).toHaveLength(2);
    const rows = within(tables[0]).getAllByRole("row");
    expect(within(rows[0]).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Phase", "When", "Owner"]);
    // (this draft has no meeting left, so the server flags every figure: "check this" is added to those)
    expect(within(rows[2]).getAllByRole("cell").map((c) => c.textContent?.replace(" (check this)", ""))).toEqual(["Training", "Week 2", "[need: trainer]"]);
  });

  it("labels a draft older than the meeting, and one whose meetings are gone", () => {
    const { unmount } = show(stale());
    expect(screen.getByText(/older than the meeting/)).toBeTruthy();
    unmount();
    show(orphan());
    expect(screen.getByText(/no longer available to you/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Line/ })).toBeNull();
    expect(screen.getAllByText("not available").length).toBeGreaterThan(0);
  });
});

describe("Copy", () => {
  it("copies the draft as plain text and warns about the blank still to fill", async () => {
    const writeText = clipboard();
    show(email());
    await userEvent.click(action("Copy"));
    expect(writeText).toHaveBeenCalledWith(blocksToText(email().blocks));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Copied with 1 blank still to fill"));
  });

  it("says nothing extra when there is no blank", async () => {
    const writeText = clipboard();
    show({ ...saved(), checks: { blanks: [], flags: [] } } as DocumentDraft);
    await userEvent.click(action("Copy"));
    expect(writeText).toHaveBeenCalled();
    expect(screen.queryByText(/still to fill/)).toBeNull();
  });
});

describe("Open in your mail app", () => {
  it("is a mailto link with the subject and the body for an email draft", () => {
    show(email());
    const link = screen.getByRole("link", { name: /Open in mail app/ }) as HTMLAnchorElement;
    const query = new URLSearchParams(link.getAttribute("href")!.slice("mailto:?".length));
    expect(query.get("subject")).toBe("Thanks for today, next steps on the onboarding");
    expect(query.get("body")).toContain("Hi Daniel, thank you for your time today.");
    expect(query.get("body")).not.toContain("Subject:");
  });

  it("is not offered for a document", () => {
    show(proposal());
    expect(screen.queryByText(/Open in mail app/)).toBeNull();
  });

  it("falls back to Copy, and says so, when the email is too long for a link", async () => {
    const writeText = clipboard();
    const long = { ...email(), blocks: [{ type: "paragraph", text: "word ".repeat(1000), sources: [] }] } as DocumentDraft;
    show(long);
    expect(screen.queryByRole("link", { name: /Open in mail app/ })).toBeNull();
    expect(screen.getByText(/too long for a mail link/i)).toBeTruthy();
    await userEvent.click(action("Open in mail app"));
    expect(writeText).toHaveBeenCalled();
  });
});

describe("the Download menu", () => {
  it("offers CSV and Excel only when the draft has a table, one CSV per table", () => {
    const { unmount } = show(proposal());
    const menu = screen.getByRole("group", { name: "Download" });
    const names = within(menu).getAllByRole("button").map((b) => b.textContent);
    expect(names.join("|")).toMatch(/Word/);
    expect(names.join("|")).toMatch(/Excel/);
    expect(names.filter((n) => /CSV/.test(n ?? ""))).toHaveLength(2);
    unmount();
    show(email());
    expect(screen.queryByText(/Excel/)).toBeNull();
    expect(screen.queryByText(/CSV/)).toBeNull();
    expect(screen.getByText(/Word document/)).toBeTruthy();
  });

  it("downloads the file the server builds and warns when blanks remain", async () => {
    const blob = new Blob(["x"]);
    mocks.export.mockResolvedValue({ blob, filename: "Proposal for Acme Ltd.csv" });
    show(proposal());
    await userEvent.click(screen.getByRole("button", { name: /CSV.*Investment|Investment.*CSV|CSV.*table 2/ }));
    expect(mocks.export).toHaveBeenCalledWith(proposal().id, "csv", 1);
    expect(mocks.save).toHaveBeenCalledWith(blob, "Proposal for Acme Ltd.csv");
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("2 blanks still to fill"));
  });

  it("shows why a file could not be made", async () => {
    mocks.export.mockRejectedValue(new ApiError(503, "export_busy", "Another file is being prepared. Try again in a few seconds."));
    show(proposal());
    await userEvent.click(screen.getByRole("button", { name: /PDF/ }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Another file is being prepared"));
    expect(mocks.save).not.toHaveBeenCalled();
  });
});

describe("editing", () => {
  it("edits a paragraph, keeps its sources, and shows what the server saved", async () => {
    mocks.edit.mockResolvedValue(saved());
    show(email());
    await userEvent.click(action("Edit"));
    const box = screen.getByRole("textbox", { name: "Block 3 text" });
    await userEvent.clear(box);
    await userEvent.type(box, "Rewritten.");
    await userEvent.click(action("Save changes"));
    const [id, body] = mocks.edit.mock.calls[0];
    expect(id).toBe(email().id);
    expect(body.blocks).toHaveLength(email().blocks.length);
    expect(body.blocks[2].text).toBe("Rewritten.");
    expect(body.blocks[2].sources).toEqual((email().blocks[2] as { sources: unknown[] }).sources); // drafted from the same lines
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: "Next steps" })).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
  });

  it("leaves the draft as it was on Cancel", async () => {
    show(email());
    await userEvent.click(action("Edit"));
    await userEvent.type(screen.getByRole("textbox", { name: "Block 2 text" }), " more");
    await userEvent.click(action("Cancel"));
    expect(mocks.edit).not.toHaveBeenCalled();
    expect(screen.getByText("Hi Daniel, thank you for your time today.")).toBeTruthy();
  });

  it("adds, moves and deletes blocks, a new block with no sources", async () => {
    mocks.edit.mockResolvedValue(saved());
    show(email());
    await userEvent.click(action("Edit"));
    await userEvent.click(action("Add paragraph"));
    const added = screen.getByRole("textbox", { name: "Block 5 text" });
    await userEvent.type(added, "A new thought.");
    await userEvent.click(action("Move block 5 up"));
    await userEvent.click(action("Delete block 1"));
    await userEvent.click(action("Save changes"));
    const blocks = mocks.edit.mock.calls[0][1].blocks;
    expect(blocks.map((b: { type: string }) => b.type)).toEqual(["paragraph", "paragraph", "paragraph", "bullets"]);
    expect(blocks.map((b: { text?: string }) => b.text?.slice(0, 14))).toEqual(["Hi Daniel, tha", "As agreed, the", "A new thought.", undefined]);
    const fresh = blocks.find((b: { text?: string }) => b.text === "A new thought.");
    expect(fresh.sources).toEqual([]);
  });

  it("edits a table: a cell, a new row, a new column, and removes a row", async () => {
    mocks.edit.mockResolvedValue(proposal());
    show(proposal());
    await userEvent.click(action("Edit"));
    await userEvent.clear(screen.getByRole("textbox", { name: "Block 4, row 1, column 1" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Block 4, row 1, column 1" }), "Kick-off");
    await userEvent.click(action("Add row to block 4"));
    await userEvent.click(action("Add column to block 4"));
    await userEvent.click(action("Remove row 2 of block 4"));
    await userEvent.click(action("Save changes"));
    const table = mocks.edit.mock.calls[0][1].blocks[3];
    expect(table.columns).toEqual(["Phase", "When", "Owner", ""]);
    expect(table.rows).toHaveLength(2); // two rows, one removed, one added
    expect(table.rows[0][0].text).toBe("Kick-off");
    expect(table.rows.every((row: unknown[]) => row.length === 4)).toBe(true);
  });

  it("refuses to remove the last column", async () => {
    show({ ...proposal(), blocks: [{ type: "table", columns: ["Only"], rows: [[{ text: "x", sources: [] }]] }] } as DocumentDraft);
    await userEvent.click(action("Edit"));
    expect((action("Remove column 1 of block 1") as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows the server's words when it cannot save, and stays in the editor", async () => {
    const refusal = recipesCapture("16-patch-bad-table.json").response.body as { error: { code: string; message: string } };
    mocks.edit.mockRejectedValue(new ApiError(422, refusal.error.code, refusal.error.message));
    show(email());
    await userEvent.click(action("Edit"));
    await userEvent.click(action("Save changes"));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Those blocks cannot be saved"));
    expect(screen.getByRole("button", { name: "Save changes" })).toBeTruthy();
  });
});

describe("Regenerate", () => {
  it("asks for a new draft with the same recipe, meetings and choices, and opens it", async () => {
    const made = recipesBody<DocumentDraft>("07-create.json");
    mocks.create.mockResolvedValue(made);
    show({ ...email(), options: { tone: "friendly", recipient: "Daniel" }, use_brief: false });
    await userEvent.click(action("Regenerate"));
    expect(mocks.create).toHaveBeenCalledWith({
      recipe_key: "follow_up_email",
      meeting_ids: [1],
      options: { tone: "friendly", recipient: "Daniel" },
      use_brief: false,
    }, undefined);
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(`/documents/${made.id}`));
  });

  it("asks in the workspace the draft was made in, whichever workspace the tab is on", async () => {
    mocks.create.mockResolvedValue(recipesBody<DocumentDraft>("07-create.json"));
    show({ ...email(), team: { id: 4, name: "Platform team" } });
    await userEvent.click(action("Regenerate"));
    expect(mocks.create.mock.calls[0][1]).toBe(4);
  });

  it("says why when the day's limit is reached, and stays", async () => {
    const refusal = recipesCapture("39-create-over-the-cap.json").response.body as { error: { code: string; message: string } };
    mocks.create.mockRejectedValue(new ApiError(429, refusal.error.code, refusal.error.message));
    show(email());
    await userEvent.click(action("Regenerate"));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("You have made 6 documents today"));
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it("is not offered when the meetings it came from are gone", () => {
    show(orphan());
    expect(screen.queryByRole("button", { name: "Regenerate" })).toBeNull();
  });
});

describe("Discard", () => {
  it("asks first, and keeps the draft on Keep", async () => {
    show(email());
    await userEvent.click(action("Discard"));
    expect(screen.getByText(/Discard this draft\?/)).toBeTruthy();
    await userEvent.click(action("Keep"));
    expect(mocks.remove).not.toHaveBeenCalled();
    expect(screen.queryByText(/Discard this draft\?/)).toBeNull();
  });

  it("deletes the draft and goes back to its meeting", async () => {
    mocks.remove.mockResolvedValue(undefined);
    show(email());
    await userEvent.click(action("Discard"));
    await userEvent.click(action("Discard draft"));
    expect(mocks.remove).toHaveBeenCalledWith(email().id);
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/meetings/1"));
  });
});

describe("DocumentBody", () => {
  it("mounts the board screen for a board, and draws no block for it", () => {
    render(<DocumentBody doc={{ ...email(), output_type: "board", blocks: [] }} blocks={[]} editing={false} onBlocks={() => {}} />);
    expect(screen.getByRole("button", { name: /download png/i })).toBeTruthy(); // the board screen's bar
    expect(document.querySelector(".doc-body")).toBeNull();
  });
});
