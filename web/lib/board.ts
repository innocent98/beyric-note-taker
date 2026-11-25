import type { components } from "./api/schema";

// The board of a meeting (docs/fe-integration-guide-recipes-board.md): a draft whose blocks are
// columns of cards, edited one operation at a time with PATCH /api/documents/{id}/board.
//
// The numbers of that route: `column` is a column's place on the board; `card` counts cards
// THROUGH the columns from 0 (card 2 is the first card of the second column when the first holds
// two); `index` is a place inside the target column once the moved card has left it. The helpers
// here turn "this card, there" into that operation, for a drag and for the "Move to column…" menu
// alike, so both send the same thing.
type Schemas = components["schemas"];

export type BoardColumn = Schemas["Column"];
export type BoardCard = Schemas["Card"];
export type CardKind = BoardCard["kind"];
export type BoardOp =
  | Schemas["MoveCard"]
  | Schemas["RenameColumn"]
  | Schemas["AddColumn"]
  | Schemas["DeleteColumn"]
  | Schemas["AddCard"]
  | Schemas["EditCard"]
  | Schemas["DeleteCard"]
  | Schemas["Recolour"];
export type MoveOp = Schemas["MoveCard"];
type Blocks = Schemas["DocumentOut"]["blocks"];
// What the board route answers: the whole draft (its `updated_at` is the next call's revision).
export type BoardDocument = Schemas["DocumentOut"];

export const MAX_COLUMNS = 12;
export const MAX_CARDS = 30;
export const KINDS: { kind: CardKind; label: string }[] = [
  { kind: "decision", label: "Decision" },
  { kind: "action", label: "Action" },
  { kind: "question", label: "Question" },
  { kind: "note", label: "Note" },
];
export const KIND_LABEL: Record<CardKind, string> = {
  decision: "Decision",
  action: "Action",
  question: "Question",
  note: "Note",
};

export type CardAt = { column: number; card: number };

export function columnsOf(blocks: Blocks): BoardColumn[] {
  return blocks.filter((b): b is BoardColumn => b.type === "column");
}

// The `card` number of the card at place `card` of column `column`.
export function cardNumber(columns: BoardColumn[], column: number, card: number): number {
  let before = 0;
  for (let c = 0; c < column; c++) before += columns[c].cards.length;
  return before + card;
}

export function moveOp(columns: BoardColumn[], from: CardAt, to: { column: number; index: number }): MoveOp {
  return { op: "move_card", card: cardNumber(columns, from.column, from.card), to_column: to.column, index: to.index };
}

// "Move to column…": to the end of the chosen column.
export function menuMove(columns: BoardColumn[], from: CardAt, toColumn: number): MoveOp {
  const room = columns[toColumn].cards.length - (toColumn === from.column ? 1 : 0);
  return moveOp(columns, from, { column: toColumn, index: room });
}

// The board as the server will make it after `op`, so a dropped card does not wait for the answer.
export function applyMove(columns: BoardColumn[], op: MoveOp): BoardColumn[] {
  const next = columns.map((c) => ({ ...c, cards: [...c.cards] }));
  let seen = 0;
  for (const column of next) {
    if (op.card < seen + column.cards.length) {
      const [moving] = column.cards.splice(op.card - seen, 1);
      next[op.to_column].cards.splice(op.index, 0, moving);
      return next;
    }
    seen += column.cards.length;
  }
  return columns;
}

// ---- where a dragged card lands ----

// How far outside a column the pointer may be and still mean it (the gap between two columns).
const GAP = 24;

export type Box = { top: number; bottom: number };
export type ColumnBox = { left: number; right: number; top: number; bottom: number; cards: Box[] };

// The column under the pointer (the nearest one when it is in the gap between two, the left one on
// a tie; none when it is above, below or beside every column) and the place among that column's
// cards: how many of them (not counting the dragged one) have their middle above the pointer.
export function dropTarget(
  point: { x: number; y: number },
  columns: ColumnBox[],
  dragging: CardAt | null,
): { column: number; index: number } | null {
  const inside = columns.findIndex(
    (b) => point.x >= b.left && point.x <= b.right && point.y >= b.top && point.y <= b.bottom,
  );
  let target = inside;
  if (target < 0) {
    let best = Infinity;
    columns.forEach((b, i) => {
      if (point.y < b.top || point.y > b.bottom) return;
      const away = point.x < b.left ? b.left - point.x : point.x - b.right;
      if (away < best) {
        best = away;
        target = i;
      }
    });
    if (target < 0 || best > GAP) return null;
  }
  let index = 0;
  columns[target].cards.forEach((box, i) => {
    if (dragging && dragging.column === target && dragging.card === i) return;
    if ((box.top + box.bottom) / 2 < point.y) index++;
  });
  return { column: target, index };
}
