// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BoardView } from "@/components/app/board-view";
import { ApiError } from "@/lib/api/client";
import type { BoardDocument } from "@/lib/board";

// The board screen: columns of cards, drag with mouse and touch, the "Move to column…" menu that
// does the same by keyboard, rename and add, recolour by kind, the PNG. The document is shaped like
// the API's `DocumentOut` (docs/fe-captures/recipes/board/).

const mocks = vi.hoisted(() => ({ edit: vi.fn(), png: vi.fn(), save: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { board: { edit: mocks.edit } } }));
vi.mock("@/lib/board-png", async (original) => ({
  ...(await original<typeof import("@/lib/board-png")>()),
  boardPng: mocks.png,
}));
vi.mock("@/lib/save-file", () => ({ saveBlob: mocks.save }));

const card = (text: string, kind = "note", owner: string | null = null, sources: unknown[] = []) => ({
  text,
  kind,
  owner,
  sources,
});
const column = (title: string, ...cards: ReturnType<typeof card>[]) => ({ type: "column", title, cards });

function doc(over: Partial<BoardDocument> = {}): BoardDocument {
  return {
    id: 7,
    title: "Onboarding board",
    output_type: "board",
    status: "ready",
    edited: false,
    meeting_ids: [3],
    created_at: "2026-11-24T12:00:00Z",
    updated_at: "2026-11-24T12:00:05.123456Z",
    blocks: [
      column(
        "Pricing",
        card("Onboarding is 50,000 naira", "decision", null, [
          { kind: "line", meeting_id: 3, line: 2, quote: "We want onboarding", ref: null },
        ]),
        card("Who signs?", "question"),
      ),
      column("Next steps", card("Send the plan", "action", "Ada")),
      column("Later"),
    ],
    checks: { blanks: [], flags: [] },
    stale: false,
    sources_available: true,
    left_out_count: 0,
    failure_code: null,
    failure_message: null,
    recipe_id: null,
    recipe_key: "board",
    ...over,
  } as unknown as BoardDocument;
}

// The page that holds the document (S2's): takes what the board route answers.
function Page({
  initial = doc(),
  canEdit = true,
  onStale = () => {},
}: {
  initial?: BoardDocument;
  canEdit?: boolean;
  onStale?: () => void;
}) {
  const [current, setCurrent] = useState(initial);
  return <BoardView doc={current} canEdit={canEdit} onSaved={setCurrent} onStale={onStale} />;
}

function answer(over: Partial<BoardDocument>) {
  return doc({ updated_at: "2026-11-24T12:01:00Z", edited: true, ...over });
}

const col = (name: string) => screen.getByRole("region", { name });
const cardsIn = (name: string) => within(col(name)).queryAllByTestId("board-card").map((c) => within(c).getByTestId("card-text").textContent);

// jsdom lays nothing out. Three columns 300 apart, each 280 wide and 400 high; the cards of a
// column 50 high from 40 down, 60 apart.
function layOut() {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const el = this as HTMLElement;
    if (el.dataset?.boardColumn !== undefined) {
      const i = Number(el.dataset.boardColumn);
      return { left: i * 300, right: i * 300 + 280, top: 0, bottom: 400, width: 280, height: 400, x: i * 300, y: 0 } as DOMRect;
    }
    if (el.dataset?.boardCard !== undefined) {
      const k = Number(el.dataset.boardCard);
      return { left: 0, right: 0, top: 40 + k * 60, bottom: 90 + k * 60, width: 0, height: 50, x: 0, y: 0 } as DOMRect;
    }
    return { left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0 } as DOMRect;
  });
}

// A drag as the browser sends it: pointer events (jsdom has none of its own, so mouse events carry the type).
function pointer(type: string, x: number, y: number, kind: "mouse" | "touch" = "mouse") {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0 });
  Object.defineProperty(event, "pointerType", { value: kind });
  Object.defineProperty(event, "pointerId", { value: 1 });
  return event;
}
function drag(handle: Element, to: { x: number; y: number }, kind: "mouse" | "touch" = "mouse") {
  fireEvent(handle, pointer("pointerdown", 100, 60, kind));
  fireEvent(document, pointer("pointermove", to.x, to.y, kind));
  fireEvent(document, pointer("pointerup", to.x, to.y, kind));
}

beforeEach(() => {
  mocks.edit.mockReset();
  mocks.png.mockReset();
  mocks.save.mockReset();
  layOut();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("the picture", () => {
  it("shows the columns with their card counts, and each card's kind, text, owner and source", () => {
    render(<Page />);
    expect(screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"))).toEqual(["Pricing", "Next steps", "Later"]);
    expect(within(col("Pricing")).getByText("2")).toBeTruthy();
    expect(cardsIn("Pricing")).toEqual(["Onboarding is 50,000 naira", "Who signs?"]);
    const first = within(col("Pricing")).getAllByTestId("board-card")[0];
    expect(within(first).getByText("Decision")).toBeTruthy();
    expect(within(first).getByRole("link", { name: /line 2/i }).getAttribute("href")).toBe("/meetings/3?tab=transcript&line=2");
    expect(within(col("Next steps")).getByText("Ada")).toBeTruthy();
    expect(within(col("Later")).getByText(/no cards yet/i)).toBeTruthy();
  });

  it("colours a card by its kind", () => {
    render(<Page />);
    const kinds = screen.getAllByTestId("board-card").map((c) => c.className.match(/bd-kind-(\w+)/)?.[1]);
    expect(kinds).toEqual(["decision", "question", "action"]);
  });

  it("shows a card's words as words, never as markup", () => {
    const d = doc({ blocks: [column("A", card("<b>bold</b> <img src=x onerror=alert(1)>"))] as never });
    const { container } = render(<Page initial={d} />);
    expect(container.querySelector("b, img")).toBeNull();
    expect(screen.getByText("<b>bold</b> <img src=x onerror=alert(1)>")).toBeTruthy();
  });
});

describe("moving a card by dragging", () => {
  it("with the mouse: the card is in its new column at once and the server is told the move", async () => {
    let finish: (d: BoardDocument) => void = () => {};
    mocks.edit.mockReturnValue(new Promise<BoardDocument>((resolve) => (finish = resolve)));
    render(<Page />);
    const handle = within(col("Pricing")).getByRole("button", { name: /drag.*onboarding is 50,000 naira/i });
    drag(handle, { x: 400, y: 300 }); // over "Next steps", below its card
    expect(mocks.edit).toHaveBeenCalledWith(7, "2026-11-24T12:00:05.123456Z", { op: "move_card", card: 0, to_column: 1, index: 1 });
    expect(cardsIn("Next steps")).toEqual(["Send the plan", "Onboarding is 50,000 naira"]); // before the answer
    expect(cardsIn("Pricing")).toEqual(["Who signs?"]);
    await act(async () => finish(answer({ blocks: [column("Pricing", card("Who signs?", "question")), column("Next steps", card("Send the plan", "action", "Ada"), card("Onboarding is 50,000 naira", "decision")), column("Later")] as never })));
    expect(cardsIn("Next steps")).toEqual(["Send the plan", "Onboarding is 50,000 naira"]);
  });

  it("with a finger: the same move", async () => {
    mocks.edit.mockResolvedValue(answer({}));
    render(<Page />);
    const handle = within(col("Pricing")).getByRole("button", { name: /drag.*who signs/i });
    drag(handle, { x: 650, y: 200 }, "touch"); // into the empty column
    expect(mocks.edit).toHaveBeenCalledWith(7, expect.any(String), { op: "move_card", card: 1, to_column: 2, index: 0 });
    await waitFor(() => expect(mocks.edit).toHaveBeenCalledTimes(1));
  });

  it("highlights the column it is over while it is dragged, and nothing once dropped", () => {
    mocks.edit.mockReturnValue(new Promise(() => {}));
    render(<Page />);
    const handle = within(col("Pricing")).getByRole("button", { name: /drag.*onboarding/i });
    fireEvent(handle, pointer("pointerdown", 100, 60));
    fireEvent(document, pointer("pointermove", 400, 200));
    expect(col("Next steps").className).toContain("tk-col-over");
    fireEvent(document, pointer("pointerup", 400, 200));
    expect(col("Next steps").className).not.toContain("tk-col-over");
  });

  it("changes nothing when dropped where it was or outside every column", () => {
    render(<Page />);
    const handle = within(col("Pricing")).getByRole("button", { name: /drag.*onboarding/i });
    drag(handle, { x: 100, y: 45 }); // back on its own place
    drag(handle, { x: 900, y: 900 }); // nowhere
    expect(mocks.edit).not.toHaveBeenCalled();
    expect(cardsIn("Pricing")).toEqual(["Onboarding is 50,000 naira", "Who signs?"]);
  });

  it("gives the card back where it was when the server refuses the move", async () => {
    mocks.edit.mockRejectedValue(new ApiError(500, "server_error", "Something went wrong."));
    render(<Page />);
    drag(within(col("Pricing")).getByRole("button", { name: /drag.*onboarding/i }), { x: 400, y: 300 });
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(cardsIn("Pricing")).toEqual(["Onboarding is 50,000 naira", "Who signs?"]);
    expect(cardsIn("Next steps")).toEqual(["Send the plan"]);
  });
});

describe("the Move to column… menu", () => {
  it("lists the other columns; Move then does what the drag does, to the end of the column", async () => {
    mocks.edit.mockResolvedValue(answer({}));
    render(<Page />);
    const menu = within(col("Pricing")).getAllByRole("combobox", { name: /move.*to column/i })[0];
    expect(within(menu).getAllByRole("option").map((o) => o.textContent)).toEqual(["Move to column…", "Next steps", "Later"]);
    await userEvent.selectOptions(menu, "Next steps");
    expect(mocks.edit).not.toHaveBeenCalled(); // choosing is not moving
    await userEvent.click(within(col("Pricing")).getByRole("button", { name: /^move$/i }));
    expect(mocks.edit).toHaveBeenCalledWith(7, "2026-11-24T12:00:05.123456Z", { op: "move_card", card: 0, to_column: 1, index: 1 });
  });

  it("sends exactly the call the drag to the same place sends", async () => {
    mocks.edit.mockResolvedValue(answer({}));
    const { unmount } = render(<Page />);
    drag(within(col("Pricing")).getByRole("button", { name: /drag.*who signs/i }), { x: 650, y: 200 });
    const dragged = mocks.edit.mock.calls[0];
    await waitFor(() => expect(mocks.edit).toHaveBeenCalledTimes(1));
    unmount();
    mocks.edit.mockClear();
    render(<Page />);
    const menu = within(col("Pricing")).getAllByRole("combobox", { name: /move.*who signs/i })[0];
    await userEvent.selectOptions(menu, "Later");
    await userEvent.click(screen.getByRole("button", { name: /^move$/i }));
    expect(mocks.edit.mock.calls[0]).toEqual(dragged);
  });

  it("is a plain select that Tab reaches, so a keyboard and a screen reader can use it", async () => {
    mocks.edit.mockResolvedValue(answer({}));
    render(<Page />);
    const menu = within(col("Next steps")).getByRole("combobox", { name: /move.*send the plan/i }) as HTMLSelectElement;
    expect(menu.tagName).toBe("SELECT");
    for (let i = 0; i < 20 && document.activeElement !== menu; i++) await userEvent.tab();
    expect(document.activeElement).toBe(menu);
    await userEvent.selectOptions(menu, "Pricing");
    expect(mocks.edit).not.toHaveBeenCalled();
    await userEvent.keyboard("{Tab}{Enter}"); // the Move button is next in the tab order
    expect(mocks.edit).toHaveBeenCalledWith(7, expect.any(String), { op: "move_card", card: 2, to_column: 0, index: 2 });
  });
});

describe("a screen that cannot change the board", () => {
  it("has no handle, no menu and no controls to add, only the PNG", () => {
    render(<Page canEdit={false} />);
    expect(screen.queryByRole("button", { name: /drag/i })).toBeNull();
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("button", { name: /add column/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /edit|rename|delete/i })).toBeNull();
    expect(screen.getByRole("button", { name: /download png/i })).toBeTruthy();
    expect(screen.getByText(/only the person who made this board can change it/i)).toBeTruthy();
  });
});

describe("columns", () => {
  it("renames one", async () => {
    mocks.edit.mockResolvedValue(answer({}));
    render(<Page />);
    await userEvent.click(screen.getByRole("button", { name: "Rename column Next steps" }));
    const input = screen.getByRole("textbox", { name: /column name/i });
    await userEvent.clear(input);
    await userEvent.type(input, "Actions{Enter}");
    expect(mocks.edit).toHaveBeenCalledWith(7, expect.any(String), { op: "rename_column", column: 1, title: "Actions" });
  });

  it("adds one at the end", async () => {
    mocks.edit.mockResolvedValue(answer({}));
    render(<Page />);
    await userEvent.click(screen.getByRole("button", { name: /add column/i }));
    await userEvent.type(screen.getByRole("textbox", { name: /column name/i }), "Risks");
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));
    expect(mocks.edit).toHaveBeenCalledWith(7, expect.any(String), { op: "add_column", title: "Risks" });
  });

  it("deletes one only after asking, with its cards named", async () => {
    mocks.edit.mockResolvedValue(answer({}));
    render(<Page />);
    await userEvent.click(screen.getByRole("button", { name: "Delete column Pricing" }));
    expect(screen.getByText(/delete “pricing” and its 2 cards/i)).toBeTruthy();
    expect(mocks.edit).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    expect(mocks.edit).toHaveBeenCalledWith(7, expect.any(String), { op: "delete_column", column: 0 });
  });

  it("stops at twelve columns, and keeps the last one", () => {
    const twelve = doc({ blocks: Array.from({ length: 12 }, (_, i) => column(`T${i}`)) as never });
    const { unmount } = render(<Page initial={twelve} />);
    expect((screen.getByRole("button", { name: /add column/i }) as HTMLButtonElement).disabled).toBe(true);
    unmount();
    render(<Page initial={doc({ blocks: [column("Only", card("x"))] as never })} />);
    expect((screen.getByRole("button", { name: "Delete column Only" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("cards", () => {
  it("adds one to a column, with its kind and owner", async () => {
    mocks.edit.mockResolvedValue(answer({}));
    render(<Page />);
    await userEvent.click(within(col("Later")).getByRole("button", { name: /add card/i }));
    await userEvent.type(screen.getByRole("textbox", { name: /card text/i }), "Call Daniel");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /^kind$/i }), "action");
    await userEvent.type(screen.getByRole("textbox", { name: /owner/i }), "Ada");
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));
    expect(mocks.edit).toHaveBeenCalledWith(7, expect.any(String), {
      op: "add_card",
      column: 2,
      text: "Call Daniel",
      kind: "action",
      owner: "Ada",
    });
  });

  it("will not add an empty card", async () => {
    render(<Page />);
    await userEvent.click(within(col("Later")).getByRole("button", { name: /add card/i }));
    expect((screen.getByRole("button", { name: /^add$/i }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("stops at thirty cards in a column", () => {
    const full = doc({ blocks: [column("A", ...Array.from({ length: 30 }, (_, i) => card(`c${i}`)))] as never });
    render(<Page initial={full} />);
    expect((screen.getByRole("button", { name: /add card/i }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("edits the text and the owner, sending only what changed", async () => {
    mocks.edit.mockResolvedValue(answer({}));
    render(<Page />);
    await userEvent.click(within(col("Next steps")).getByRole("button", { name: /edit.*send the plan/i }));
    const text = screen.getByRole("textbox", { name: /card text/i });
    await userEvent.clear(text);
    await userEvent.type(text, "Send the plan on Friday");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
    expect(mocks.edit).toHaveBeenCalledWith(7, expect.any(String), { op: "edit_card", card: 2, text: "Send the plan on Friday" });
  });

  it("clears an owner by emptying the box", async () => {
    mocks.edit.mockResolvedValue(answer({}));
    render(<Page />);
    await userEvent.click(within(col("Next steps")).getByRole("button", { name: /edit.*send the plan/i }));
    await userEvent.clear(screen.getByRole("textbox", { name: /owner/i }));
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
    expect(mocks.edit).toHaveBeenCalledWith(7, expect.any(String), { op: "edit_card", card: 2, owner: "" });
  });

  it("recolours by kind: a change of kind alone is a recolour", async () => {
    mocks.edit.mockResolvedValue(answer({}));
    render(<Page />);
    await userEvent.click(within(col("Pricing")).getByRole("button", { name: /edit.*who signs/i }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /^kind$/i }), "decision");
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
    expect(mocks.edit).toHaveBeenCalledWith(7, expect.any(String), { op: "recolour", card: 1, kind: "decision" });
  });

  it("deletes a card from its editor", async () => {
    mocks.edit.mockResolvedValue(answer({}));
    render(<Page />);
    await userEvent.click(within(col("Pricing")).getByRole("button", { name: /edit.*who signs/i }));
    await userEvent.click(screen.getByRole("button", { name: /delete card/i }));
    expect(mocks.edit).toHaveBeenCalledWith(7, expect.any(String), { op: "delete_card", card: 1 });
  });

  it("closes the editor without a call when nothing changed", async () => {
    render(<Page />);
    await userEvent.click(within(col("Pricing")).getByRole("button", { name: /edit.*who signs/i }));
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));
    expect(mocks.edit).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox", { name: /card text/i })).toBeNull();
  });
});

describe("a board that moved on, a save that failed", () => {
  it("on a stale screen says so, asks the page to reload and leaves the board as the page has it", async () => {
    mocks.edit.mockRejectedValue(new ApiError(409, "stale_board", "The board changed since you opened it. It has been reloaded."));
    const onStale = vi.fn();
    render(<Page onStale={onStale} />);
    drag(within(col("Pricing")).getByRole("button", { name: /drag.*onboarding/i }), { x: 400, y: 300 });
    expect((await screen.findByRole("alert")).textContent).toMatch(/changed since you opened it/i);
    expect(onStale).toHaveBeenCalledTimes(1);
    expect(cardsIn("Pricing")).toEqual(["Onboarding is 50,000 naira", "Who signs?"]);
  });

  it("says what the server said when a change cannot be made", async () => {
    mocks.edit.mockRejectedValue(new ApiError(422, "column_full", "A column holds at most 30 cards."));
    render(<Page />);
    await userEvent.click(within(col("Later")).getByRole("button", { name: /add card/i }));
    await userEvent.type(screen.getByRole("textbox", { name: /card text/i }), "x");
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/at most 30 cards/i);
  });

  it("makes one change at a time: a second drag while one is being saved is ignored", () => {
    mocks.edit.mockReturnValue(new Promise(() => {}));
    render(<Page />);
    drag(within(col("Pricing")).getByRole("button", { name: /drag.*onboarding/i }), { x: 400, y: 300 });
    const next = within(col("Next steps")).getByRole("button", { name: /drag.*send the plan/i });
    drag(next, { x: 650, y: 200 });
    expect(mocks.edit).toHaveBeenCalledTimes(1);
  });
});

describe("the PNG", () => {
  it("is built from the board on the screen and saved under its name", async () => {
    const blob = new Blob(["png"], { type: "image/png" });
    mocks.png.mockResolvedValue(blob);
    render(<Page />);
    await userEvent.click(screen.getByRole("button", { name: /download png/i }));
    expect(mocks.png).toHaveBeenCalledWith("Onboarding board", doc().blocks);
    await waitFor(() => expect(mocks.save).toHaveBeenCalledWith(blob, "Onboarding board.png"));
  });

  it("says so when the browser cannot make it", async () => {
    mocks.png.mockRejectedValue(new Error("This browser cannot draw a canvas."));
    render(<Page />);
    await userEvent.click(screen.getByRole("button", { name: /download png/i }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/could not make the png/i);
    expect(mocks.save).not.toHaveBeenCalled();
  });
});
