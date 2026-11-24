// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentView } from "@/components/app/document-view";
import { ApiError } from "@/lib/api/client";
import type { DocumentBlock, DocumentDraft } from "@/lib/api/types";
import { isHiddenSource, sourceHref, withoutHiddenSources } from "@/lib/documents";
import { recipesBody } from "./helpers";

// Sharing a draft with the team (plan task 14; lead-designed, the designer's files draw none of it) and the
// read-only screen a teammate gets. The drafts are the real captures of docs/fe-captures/recipes/ (made before
// sharing existed), with the sharing fields the contract added (`access`, `can_edit`, `owner_name`,
// `shared_with`, and `kind: "hidden"` sources) set by hand here: they are NOT captured bodies. The two
// refusal sentences are the ones in the API's own text (app/api_recipes.py).

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  edit: vi.fn(),
  create: vi.fn(),
  remove: vi.fn(),
  export: vi.fn(),
  share: vi.fn(),
  unshare: vi.fn(),
  push: vi.fn(),
  save: vi.fn(),
  boardEdit: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    documents: {
      get: mocks.get,
      edit: mocks.edit,
      create: mocks.create,
      remove: mocks.remove,
      export: mocks.export,
      share: mocks.share,
      unshare: mocks.unshare,
    },
    board: { edit: mocks.boardEdit },
  },
}));
vi.mock("@/lib/save-file", () => ({ saveBlob: mocks.save }));

const PERSONAL = "This draft was made in your own space. Make it in a team workspace to share it with that team.";
const LEFT = "You are no longer in the team this draft was made in, so it cannot be shared.";
const NOT_READY = "Only a finished draft can be shared.";
const SALES = { id: 7, name: "Sales team" };

const email = () => recipesBody<DocumentDraft>("11-get-ready.json");
const board = () => recipesBody<DocumentDraft>("board/04-get-board.json");
const proposal = () => recipesBody<DocumentDraft>("exports/24-the-draft.json");
const sharedWith = (draft: DocumentDraft, teams = [SALES]): DocumentDraft => ({ ...draft, shared_with: teams });
const asReader = (draft: DocumentDraft): DocumentDraft => ({ ...draft, access: "shared", can_edit: false, owner_name: "Ada Obi", shared_with: [] });
// A draft read back as its creator has the team it was made in and no reason against sharing it.
const show = (draft: DocumentDraft) => render(<DocumentView initial={{ team: SALES, share_blocked: null, ...draft }} />);
const button = (name: string | RegExp) => screen.getByRole("button", { name });
const dialog = () => within(screen.getByRole("dialog"));

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
    this.removeAttribute("open");
  };
});
afterEach(cleanup);

describe("the share dialog", () => {
  it("says what sharing reaches, and names the team it shares with", async () => {
    show(email());
    await userEvent.click(button("Share with team"));
    const d = dialog();
    expect(d.getByText("Teammates will read this draft even if they cannot open the meeting.")).toBeTruthy();
    expect(d.getByText(/They cannot edit it\. You can stop sharing at any time/)).toBeTruthy();
    expect(d.getByText(/title and text can reveal what was said in it/)).toBeTruthy();
    expect(d.getByText(/generic title instead of this one/)).toBeTruthy();
    expect(await d.findByRole("button", { name: "Share with Sales team" })).toBeTruthy();
    expect(d.queryByRole("button", { name: "Stop sharing" })).toBeNull();
  });

  it("is offered to the owner of a ready draft only: not while it is being written, not while editing", async () => {
    show({ ...email(), status: "writing" });
    expect(screen.queryByRole("button", { name: /Share with team|Shared/ })).toBeNull();
    cleanup();
    show(email());
    await userEvent.click(button("Edit"));
    expect(screen.queryByRole("button", { name: "Share with team" })).toBeNull();
  });

  it("shares with the named team, shows 'Shared' on the toolbar and says with whom", async () => {
    mocks.share.mockResolvedValue(sharedWith(email()));
    show(email());
    await userEvent.click(button("Share with team"));
    await userEvent.click(await dialog().findByRole("button", { name: "Share with Sales team" }));
    expect(mocks.share).toHaveBeenCalledWith(email().id);
    await waitFor(() => expect(button("Shared")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Share with team" })).toBeNull();
    expect(screen.getByText("Shared with Sales team")).toBeTruthy();
  });

  it("stops sharing: Stop sharing is offered on a shared draft, calls the API and the toolbar goes back", async () => {
    mocks.unshare.mockResolvedValue(undefined);
    show(sharedWith(email(), [SALES, { id: 9, name: "Support" }]));
    expect(screen.getByText("Shared with Sales team, Support")).toBeTruthy();
    await userEvent.click(button("Shared"));
    expect(dialog().getByText("Shared with Sales team, Support")).toBeTruthy();
    await userEvent.click(dialog().getByRole("button", { name: "Stop sharing" }));
    expect(mocks.unshare).toHaveBeenCalledWith(email().id);
    await waitFor(() => expect(button("Share with team")).toBeTruthy());
    expect(screen.queryByText(/Shared with/)).toBeNull();
  });

  it("shows the server's sentence when the draft is not ready or the team is the person's own space", async () => {
    show(email());
    await userEvent.click(button("Share with team"));
    mocks.share.mockRejectedValueOnce(new ApiError(409, "personal_team", PERSONAL));
    await userEvent.click(await dialog().findByRole("button", { name: "Share with Sales team" }));
    expect((await dialog().findByRole("alert")).textContent).toContain(PERSONAL);
    mocks.share.mockRejectedValueOnce(new ApiError(409, "not_ready", NOT_READY));
    await userEvent.click(dialog().getByRole("button", { name: "Share with Sales team" }));
    await waitFor(() => expect(dialog().getByRole("alert").textContent).toContain(NOT_READY));
    expect(screen.queryByText("Shared with Sales team")).toBeNull();
  });

  it("says plainly that sharing or stopping failed for any other reason, and lets the person try again", async () => {
    show(email());
    await userEvent.click(button("Share with team"));
    mocks.share.mockRejectedValueOnce(new ApiError(500, "server_error", "boom"));
    await userEvent.click(await dialog().findByRole("button", { name: "Share with Sales team" }));
    expect((await dialog().findByRole("alert")).textContent).toBe("We could not share this draft. Try again.");
    mocks.share.mockResolvedValueOnce(sharedWith(email()));
    await userEvent.click(dialog().getByRole("button", { name: "Share with Sales team" }));
    await waitFor(() => expect(button("Shared")).toBeTruthy());
  });

  it("says plainly when stopping failed", async () => {
    mocks.unshare.mockRejectedValue(new TypeError("offline"));
    show(sharedWith(email()));
    await userEvent.click(button("Shared"));
    await userEvent.click(dialog().getByRole("button", { name: "Stop sharing" }));
    expect((await dialog().findByRole("alert")).textContent).toBe("We could not stop sharing this draft. Try again.");
    expect(screen.getAllByText("Shared with Sales team").length).toBe(2); // still shared: the toolbar line and the dialog's
  });

  it("shows the server's reason and offers no Share when the draft cannot be shared", async () => {
    show({ ...email(), share_blocked: PERSONAL });
    await userEvent.click(button("Share with team"));
    expect(dialog().getByText(PERSONAL)).toBeTruthy();
    expect((dialog().getByRole("button", { name: "Share with Sales team" }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(dialog().getByRole("button", { name: "Share with Sales team" }));
    expect(mocks.share).not.toHaveBeenCalled();
  });

  it("names the team the draft was made in, not the tab's, and shows the 'left the team' sentence from the server", async () => {
    show({ ...email(), team: { id: 9, name: "Support" } });
    await userEvent.click(button("Share with team"));
    mocks.share.mockRejectedValueOnce(new ApiError(409, "team_left", LEFT));
    await userEvent.click(dialog().getByRole("button", { name: "Share with Support" }));
    expect((await dialog().findByRole("alert")).textContent).toContain(LEFT);
    expect(mocks.share).toHaveBeenCalledWith(email().id);
  });
});

describe("a draft a teammate shared (read-only)", () => {
  it("says who shared it, keeps Copy, the mail app and Download, and offers no Edit, Share, Regenerate or Discard", () => {
    show(asReader(email()));
    expect(screen.getByText("Shared by Ada Obi")).toBeTruthy();
    expect(button("Copy")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Open in mail app/ })).toBeTruthy();
    expect(screen.getByText("Download")).toBeTruthy();
    for (const name of [/^Edit$/, /Share with team/, /^Shared$/, /Regenerate/, /Discard/]) {
      expect(screen.queryByRole("button", { name })).toBeNull();
    }
    expect(screen.getByRole("link", { name: /Back to documents/ }).getAttribute("href")).toBe("/documents?tab=shared");
  });

  it("tells a reader who cannot open the meetings that they never could, not that they lost access", () => {
    show({ ...asReader(email()), sources_available: false });
    expect(screen.getByText(/You cannot open the meetings this draft was made from/)).toBeTruthy();
    expect(screen.queryByText(/no longer available to you/)).toBeNull();
  });

  it("downloads a file like the owner does", async () => {
    mocks.export.mockResolvedValue({ blob: new Blob(["x"]), filename: "draft.docx" });
    show(asReader(proposal()));
    await userEvent.click(screen.getByText("Download"));
    await userEvent.click(screen.getByRole("button", { name: /Word document/ }));
    expect(mocks.export).toHaveBeenCalledWith(proposal().id, "docx", 0);
  });

  it("shows the draft's own owner as the creator and no 'Shared by' on the owner's screen", () => {
    show(email());
    expect(screen.queryByText(/Shared by/)).toBeNull();
  });

  it("hands the board its read-only mode: no card can be dragged or moved", () => {
    show(asReader(board()));
    expect(screen.getByText("Only the person who made this board can change it.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Drag card/ })).toBeNull();
    expect(screen.queryByRole("combobox", { name: /to column/ })).toBeNull();
    cleanup();
    show(board());
    expect(screen.getAllByRole("button", { name: /Drag card/ }).length).toBeGreaterThan(0);
  });
});

const hidden = { kind: "hidden" as const, meeting_id: null, line: null, quote: null, ref: null };
const withHidden = (): DocumentDraft => ({
  ...asReader(email()),
  blocks: [
    { type: "paragraph", text: "Only the text is yours to read.", sources: [hidden] },
    { type: "paragraph", text: "This line is from a meeting you can open.", sources: [{ kind: "line", meeting_id: 4, line: 2, quote: "Hello", ref: null }, hidden] },
  ],
});

describe("a source the reader may not open", () => {
  it("reads 'not available' and is not a link", () => {
    show(withHidden());
    expect(screen.getAllByText("not available")).toHaveLength(2);
    const first = screen.getByText("Only the text is yours to read.").closest(".doc-block") as HTMLElement;
    expect(within(first).queryByRole("link")).toBeNull();
    expect(within(first).getByText("not available").className).toContain("doc-chip-off");
  });

  it("keeps the sources the reader can open as links beside it", () => {
    show(withHidden());
    const second = screen.getByText("This line is from a meeting you can open.").closest(".doc-block") as HTMLElement;
    expect(within(second).getByRole("link", { name: "Line 2" }).getAttribute("href")).toBe("/meetings/4?tab=transcript&line=2");
    expect(within(second).getAllByText("not available")).toHaveLength(1);
  });

  it("is told from a source with no meeting, and has no address", () => {
    expect(isHiddenSource(hidden)).toBe(true);
    expect(isHiddenSource({ kind: "meeting", meeting_id: null })).toBe(true);
    expect(isHiddenSource({ kind: "meeting", meeting_id: 3 })).toBe(false);
    expect(sourceHref(hidden)).toBeNull();
    expect(sourceHref({ kind: "line", meeting_id: null, line: 3 })).toBeNull();
    expect(sourceHref({ kind: "task", meeting_id: 3, ref: 1 })).toBe("/meetings/3");
  });

  it("is never sent back by the editor: the others are kept untouched", async () => {
    const owner = { ...email(), blocks: withHidden().blocks, can_edit: true } as DocumentDraft;
    mocks.edit.mockResolvedValue(owner);
    show(owner);
    await userEvent.click(button("Edit"));
    await userEvent.clear(screen.getByRole("textbox", { name: "Block 1 text" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Block 1 text" }), "Changed");
    await userEvent.click(button("Save changes"));
    await waitFor(() => expect(mocks.edit).toHaveBeenCalled());
    const sent = (mocks.edit.mock.calls[0][1] as { blocks: DocumentBlock[] }).blocks;
    expect(JSON.stringify(sent)).not.toContain("hidden");
    expect(sent[0]).toMatchObject({ text: "Changed", sources: [] });
    expect(sent[1]).toMatchObject({ sources: [{ kind: "line", meeting_id: 4, line: 2, quote: "Hello", ref: null }] });
  });

  it("is dropped from every kind of block, and a block without sources is left as it was", () => {
    const blocks: DocumentBlock[] = [
      { type: "bullets", items: [{ text: "a", sources: [hidden, { kind: "meeting", meeting_id: 1 }] }, { text: "b" }] },
      { type: "table", columns: ["x"], rows: [[{ text: "c", sources: [hidden] }]] },
      { type: "heading", level: 2, text: "h" },
    ];
    const kept = withoutHiddenSources(blocks);
    expect(kept[0]).toEqual({ type: "bullets", items: [{ text: "a", sources: [{ kind: "meeting", meeting_id: 1 }] }, { text: "b" }] });
    expect(kept[1]).toEqual({ type: "table", columns: ["x"], rows: [[{ text: "c", sources: [] }]] });
    expect(kept[2]).toBe(blocks[2]);
  });
});
