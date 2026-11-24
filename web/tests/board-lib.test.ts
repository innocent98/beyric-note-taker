import { describe, expect, it } from "vitest";
import {
  applyMove,
  cardNumber,
  columnsOf,
  dropTarget,
  menuMove,
  moveOp,
  type BoardColumn,
  type ColumnBox,
} from "@/lib/board";

// The helpers behind the board screen: the card numbering of PATCH /api/documents/{id}/board
// (cards counted through the columns from 0, `index` a place inside the target column once the card
// has left), and where a dragged card lands.

const card = (text: string) => ({ text, kind: "note" as const, owner: null, sources: [] });
const column = (title: string, ...cards: string[]): BoardColumn => ({ type: "column", title, cards: cards.map(card) });
const board = () => [column("A", "a1", "a2"), column("B", "b1"), column("C")];
const texts = (columns: BoardColumn[]) => columns.map((c) => c.cards.map((x) => x.text));

describe("numbering", () => {
  it("counts cards through the columns from 0", () => {
    expect(cardNumber(board(), 0, 0)).toBe(0);
    expect(cardNumber(board(), 0, 1)).toBe(1);
    expect(cardNumber(board(), 1, 0)).toBe(2);
  });

  it("keeps only the columns of a draft's blocks", () => {
    const blocks = [{ type: "heading", level: 1, text: "x" }, column("A", "a1")] as never;
    expect(columnsOf(blocks).map((c) => c.title)).toEqual(["A"]);
  });
});

describe("a move", () => {
  it("is the operation the server takes, whichever way it was asked for", () => {
    expect(moveOp(board(), { column: 0, card: 1 }, { column: 1, index: 0 })).toEqual({
      op: "move_card",
      card: 1,
      to_column: 1,
      index: 0,
    });
    expect(moveOp(board(), { column: 1, card: 0 }, { column: 0, index: 2 })).toMatchObject({ card: 2, to_column: 0, index: 2 });
  });

  it("from the menu goes to the end of the other column", () => {
    expect(menuMove(board(), { column: 0, card: 0 }, 1)).toEqual({ op: "move_card", card: 0, to_column: 1, index: 1 });
    expect(menuMove(board(), { column: 0, card: 1 }, 2)).toEqual({ op: "move_card", card: 1, to_column: 2, index: 0 });
  });

  it("is shown at once as the server will make it", () => {
    const moved = applyMove(board(), { op: "move_card", card: 0, to_column: 1, index: 1 });
    expect(texts(moved)).toEqual([["a2"], ["b1", "a1"], []]);
    const within = applyMove(board(), { op: "move_card", card: 0, to_column: 0, index: 1 });
    expect(texts(within)).toEqual([["a2", "a1"], ["b1"], []]);
  });

  it("does not change the board it was given", () => {
    const start = board();
    applyMove(start, { op: "move_card", card: 0, to_column: 2, index: 0 });
    expect(texts(start)).toEqual([["a1", "a2"], ["b1"], []]);
  });
});

describe("where a dragged card lands", () => {
  // Three columns 300 wide side by side; cards 50 high with a 10 gap, starting 40 below the top.
  const boxes = (): ColumnBox[] =>
    [2, 1, 0].map((count, i) => ({
      left: i * 300,
      right: i * 300 + 280,
      top: 0,
      bottom: 400,
      cards: Array.from({ length: count }, (_, k) => ({ top: 40 + k * 60, bottom: 90 + k * 60 })),
    }));

  it("is the column under the pointer and the place among its cards", () => {
    expect(dropTarget({ x: 100, y: 10 }, boxes(), null)).toEqual({ column: 0, index: 0 });
    expect(dropTarget({ x: 100, y: 100 }, boxes(), null)).toEqual({ column: 0, index: 1 });
    expect(dropTarget({ x: 100, y: 300 }, boxes(), null)).toEqual({ column: 0, index: 2 });
    expect(dropTarget({ x: 400, y: 300 }, boxes(), null)).toEqual({ column: 1, index: 1 });
  });

  it("is the first place of an empty column", () => {
    expect(dropTarget({ x: 650, y: 200 }, boxes(), null)).toEqual({ column: 2, index: 0 });
  });

  it("does not count the dragged card itself", () => {
    // dragging a1 (column 0, place 0) to just below a2: a2 is the only other card, so place 1
    expect(dropTarget({ x: 100, y: 200 }, boxes(), { column: 0, card: 0 })).toEqual({ column: 0, index: 1 });
    // dragging a1 over its own top half: still place 0
    expect(dropTarget({ x: 100, y: 45 }, boxes(), { column: 0, card: 0 })).toEqual({ column: 0, index: 0 });
  });

  it("is nowhere outside every column, and the nearest column in the gap between two", () => {
    expect(dropTarget({ x: 100, y: 900 }, boxes(), null)).toBeNull();
    expect(dropTarget({ x: 290, y: 10 }, boxes(), null)).toEqual({ column: 0, index: 0 });
    expect(dropTarget({ x: 295, y: 10 }, boxes(), null)).toEqual({ column: 1, index: 0 });
  });
});
