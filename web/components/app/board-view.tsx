"use client";

import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { Alert, AppLink, Button, Icon } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import {
  applyMove,
  columnsOf,
  dropTarget,
  KIND_LABEL,
  KINDS,
  MAX_CARDS,
  MAX_COLUMNS,
  menuMove,
  moveOp,
  type BoardCard,
  type BoardColumn,
  type BoardDocument,
  type BoardOp,
  type CardAt,
  type CardKind,
  type ColumnBox,
} from "@/lib/board";
import { boardFilename, boardPng } from "@/lib/board-png";
import { sourceHref, sourceLabel } from "@/lib/documents";
import { saveBlob } from "@/lib/save-file";

// The board of a meeting (docs/fe-integration-guide-recipes-board.md): a static, one-page picture,
// NOT live. Columns by theme, cards with a kind, an owner and the source they were drafted from.
// Its creator drags a card to another column with the mouse or a finger (pointer events, so one
// code path serves both; on a phone it is best effort), or uses the "Move to column…" menu on every
// card, which is a plain select a keyboard and a screen reader reach and which sends the same
// operation. Rename and add a column, add or edit a card, recolour a card by changing its kind.
// Everyone who can open the board can download it as a PNG, built here with a canvas.
//
// The screen does not own the document: the page that fetched it (`doc`) does. Each change is one
// PATCH /api/documents/{id}/board sent with the draft's `updated_at` as its revision; the answer is
// the whole draft, handed to `onSaved`. A moved card shows in its new place at once and goes back
// if the server refuses. 409 `stale_board` (the board changed in another tab) asks the page to
// reload (`onStale`). One change at a time. Card text is plain and is drawn as text.
export function BoardView({
  doc,
  canEdit,
  onSaved,
  onStale,
}: {
  doc: BoardDocument;
  canEdit: boolean;
  onSaved: (doc: BoardDocument) => void;
  onStale: () => void | Promise<void>;
}) {
  const [local, setLocal] = useState<BoardColumn[] | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState<{ from: CardAt; over: { column: number; index: number } | null } | null>(null);
  const dragRef = useRef<CardAt | null>(null);
  const boardRef = useRef<HTMLDivElement>(null);
  const [renaming, setRenaming] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [addingColumn, setAddingColumn] = useState(false);
  const [addingCard, setAddingCard] = useState<number | null>(null);
  const [editing, setEditing] = useState<CardAt | null>(null);
  const [png, setPng] = useState(false);

  const columns = local ?? columnsOf(doc.blocks);
  const dragging = drag !== null;

  async function submit(op: BoardOp, optimistic?: BoardColumn[]): Promise<boolean> {
    if (busyRef.current) return false;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    if (optimistic) setLocal(optimistic);
    try {
      const next = await api.board.edit(doc.id, doc.updated_at, op);
      setLocal(null);
      onSaved(next);
      return true;
    } catch (e) {
      setLocal(null);
      setError(sayWhy(e));
      if (e instanceof ApiError && e.code === "stale_board") await onStale();
      return false;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  // ---- dragging ----

  function boxes(): ColumnBox[] {
    const root = boardRef.current;
    if (!root) return [];
    return Array.from(root.querySelectorAll<HTMLElement>("[data-board-column]")).map((el) => {
      const rect = el.getBoundingClientRect();
      const cards = Array.from(el.querySelectorAll<HTMLElement>("[data-board-card]")).map((c) => {
        const r = c.getBoundingClientRect();
        return { top: r.top, bottom: r.bottom };
      });
      return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, cards };
    });
  }

  function startDrag(from: CardAt, event: ReactPointerEvent<HTMLElement>) {
    if (busyRef.current || !canEdit || event.button > 0) return;
    event.preventDefault();
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch {
      // not every browser captures a touch pointer; the document listeners still see the moves
    }
    dragRef.current = from;
    setDrag({ from, over: null });
  }

  useEffect(() => {
    if (!dragging) return;
    const over = (e: PointerEvent) => dropTarget({ x: e.clientX, y: e.clientY }, boxes(), dragRef.current);
    const move = (e: PointerEvent) => {
      const target = over(e);
      setDrag((d) => (d ? { ...d, over: target } : d));
    };
    const finish = (e: PointerEvent, drop: boolean) => {
      const from = dragRef.current;
      const target = drop ? over(e) : null;
      dragRef.current = null;
      setDrag(null);
      if (!from || !target || (target.column === from.column && target.index === from.card)) return;
      const op = moveOp(columns, from, target);
      void submit(op, applyMove(columns, op));
    };
    const up = (e: PointerEvent) => finish(e, true);
    const cancel = (e: PointerEvent) => finish(e, false);
    document.addEventListener("pointermove", move);
    document.addEventListener("pointerup", up);
    document.addEventListener("pointercancel", cancel);
    return () => {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
      document.removeEventListener("pointercancel", cancel);
    };
    // The listeners belong to one drag; they read the board as it was when it began.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dragging]);

  // ---- the PNG ----

  async function downloadPng() {
    setError(null);
    setPng(true);
    try {
      const blob = await boardPng(doc.title, columns);
      saveBlob(blob, boardFilename(doc.title));
    } catch {
      setError("Could not make the PNG. Try again.");
    } finally {
      setPng(false);
    }
  }

  return (
    <div className="bd">
      <div className="bd-bar">
        <span className="bd-note">{canEdit ? null : "Only the person who made this board can change it."}</span>
        <div className="bd-actions">
          {canEdit && addingColumn ? (
            <NameForm
              label="Column name"
              submit="Add"
              onSubmit={async (title) => {
                if (await submit({ op: "add_column", title })) setAddingColumn(false);
              }}
              onCancel={() => setAddingColumn(false)}
              busy={busy}
            />
          ) : canEdit ? (
            <Button
              kind="secondary"
              size="sm"
              type="button"
              disabled={busy || columns.length >= MAX_COLUMNS}
              title={columns.length >= MAX_COLUMNS ? "A board holds at most 12 columns." : undefined}
              onClick={() => setAddingColumn(true)}
            >
              <Icon name="plus" size={14} /> Add column
            </Button>
          ) : null}
          <Button kind="secondary" size="sm" type="button" disabled={png} onClick={() => void downloadPng()}>
            <Icon name="download" size={14} /> Download PNG
          </Button>
        </div>
      </div>
      {error ? <Alert kind="error">{error}</Alert> : null}
      <div className={`tk-board bd-board${dragging ? " bd-board-dragging" : ""}`} ref={boardRef} aria-busy={busy}>
        {columns.map((column, c) => (
          <section
            key={c}
            aria-label={column.title}
            data-board-column={c}
            className={`tk-col${drag?.over?.column === c ? " tk-col-over" : ""}`}
          >
            <div className="tk-col-head">
              {renaming === c ? (
                <NameForm
                  label="Column name"
                  submit="Save"
                  initial={column.title}
                  onSubmit={async (title) => {
                    if (title === column.title || (await submit({ op: "rename_column", column: c, title }))) setRenaming(null);
                  }}
                  onCancel={() => setRenaming(null)}
                  busy={busy}
                />
              ) : (
                <>
                  <span className="tk-col-label">{column.title}</span>
                  <span className="tk-col-count">{column.cards.length}</span>
                  {canEdit ? (
                    <span className="bd-col-tools">
                      <button
                        type="button"
                        className="rh-btn rh-btn-quiet"
                        aria-label={`Rename column ${column.title}`}
                        disabled={busy}
                        onClick={() => setRenaming(c)}
                      >
                        Rename
                      </button>
                      <button
                        type="button"
                        className="icon-btn icon-btn-danger"
                        aria-label={`Delete column ${column.title}`}
                        title={columns.length === 1 ? "A board keeps at least one column." : `Delete column ${column.title}`}
                        disabled={busy || columns.length === 1}
                        onClick={() => setDeleting(c)}
                      >
                        <Icon name="trash" size={14} />
                      </button>
                    </span>
                  ) : null}
                </>
              )}
            </div>
            {deleting === c ? (
              <div className="bd-confirm" role="group" aria-label={`Delete ${column.title}`}>
                <span>
                  {column.cards.length
                    ? `Delete “${column.title}” and its ${column.cards.length} ${column.cards.length === 1 ? "card" : "cards"}?`
                    : `Delete “${column.title}”?`}
                </span>
                <Button
                  kind="danger"
                  size="sm"
                  type="button"
                  disabled={busy}
                  onClick={async () => {
                    if (await submit({ op: "delete_column", column: c })) setDeleting(null);
                  }}
                >
                  Delete
                </Button>
                <Button kind="secondary" size="sm" type="button" onClick={() => setDeleting(null)}>
                  Cancel
                </Button>
              </div>
            ) : null}
            {column.cards.map((card, k) =>
              editing?.column === c && editing.card === k ? (
                <CardForm
                  key={k}
                  initial={card}
                  submit="Save"
                  busy={busy}
                  onCancel={() => setEditing(null)}
                  onDelete={async () => {
                    if (await submit({ op: "delete_card", card: cardNumberOf(columns, c, k) })) setEditing(null);
                  }}
                  onSubmit={async (next) => {
                    const op = editOp(columns, c, k, card, next);
                    if (op === null || (await submit(op))) setEditing(null);
                  }}
                />
              ) : (
                <CardView
                  key={k}
                  card={card}
                  column={c}
                  index={k}
                  columns={columns}
                  canEdit={canEdit}
                  busy={busy}
                  dragging={drag?.from.column === c && drag.from.card === k}
                  onGrab={(event) => startDrag({ column: c, card: k }, event)}
                  onMove={(to) => {
                    const op = menuMove(columns, { column: c, card: k }, to);
                    void submit(op, applyMove(columns, op));
                  }}
                  onEdit={() => {
                    setAddingCard(null);
                    setEditing({ column: c, card: k });
                  }}
                />
              ),
            )}
            {column.cards.length === 0 && addingCard !== c ? <div className="tk-empty">No cards yet</div> : null}
            {canEdit && addingCard === c ? (
              <CardForm
                submit="Add"
                busy={busy}
                onCancel={() => setAddingCard(null)}
                onSubmit={async (next) => {
                  const op: BoardOp = { op: "add_card", column: c, text: next.text, kind: next.kind };
                  if (next.owner) op.owner = next.owner;
                  if (await submit(op)) setAddingCard(null);
                }}
              />
            ) : canEdit ? (
              <button
                type="button"
                className="rh-btn bd-add-card"
                disabled={busy || column.cards.length >= MAX_CARDS}
                title={column.cards.length >= MAX_CARDS ? "A column holds at most 30 cards." : undefined}
                onClick={() => {
                  setEditing(null);
                  setAddingCard(c);
                }}
              >
                Add card
              </button>
            ) : null}
          </section>
        ))}
      </div>
    </div>
  );
}

// The words of a refusal: the board route's own sentences are written for the person (its 422s too,
// which `errorMessage` would otherwise hide as developer text).
const OWN_WORDS = new Set(["stale_board", "board_full", "column_full", "title_empty", "card_empty", "last_column", "writing", "not_ready", "not_a_board"]);
function sayWhy(e: unknown): string {
  return e instanceof ApiError && OWN_WORDS.has(e.code) ? e.message : errorMessage(e);
}

function cardNumberOf(columns: BoardColumn[], column: number, card: number): number {
  let before = 0;
  for (let c = 0; c < column; c++) before += columns[c].cards.length;
  return before + card;
}

type CardFields = { text: string; owner: string; kind: CardKind };

// The one operation an edited card needs: only what changed; a change of kind alone is a recolour;
// null when nothing changed.
function editOp(columns: BoardColumn[], c: number, k: number, was: BoardCard, next: CardFields): BoardOp | null {
  const card = cardNumberOf(columns, c, k);
  const text = next.text !== was.text;
  const owner = next.owner !== (was.owner ?? "");
  const kind = next.kind !== was.kind;
  if (!text && !owner && !kind) return null;
  if (kind && !text && !owner) return { op: "recolour", card, kind: next.kind };
  const op: BoardOp = { op: "edit_card", card };
  if (text) op.text = next.text;
  if (owner) op.owner = next.owner;
  if (kind) op.kind = next.kind;
  return op;
}

function CardView({
  card,
  column,
  index,
  columns,
  canEdit,
  busy,
  dragging,
  onGrab,
  onMove,
  onEdit,
}: {
  card: BoardCard;
  column: number;
  index: number;
  columns: BoardColumn[];
  canEdit: boolean;
  busy: boolean;
  dragging: boolean;
  onGrab: (event: ReactPointerEvent<HTMLElement>) => void;
  onMove: (toColumn: number) => void;
  onEdit: () => void;
}) {
  const sources = (card.sources ?? []).slice(0, 3);
  // The column chosen in the menu; nothing moves until Move is pressed, so a keyboard that arrows
  // through the options (a closed select fires `change` on each arrow in some browsers) moves nothing.
  const [to, setTo] = useState("");
  return (
    <div
      className={`tk-card bd-card bd-kind-${card.kind}${dragging ? " tk-card-dragging" : ""}`}
      data-board-card={index}
      data-testid="board-card"
    >
      <div className="tk-card-top">
        <span className={`bd-chip bd-chip-${card.kind}`}>{KIND_LABEL[card.kind]}</span>
        {canEdit ? (
          <button type="button" className="bd-grip" aria-label={`Drag card: ${card.text}`} title="Drag to another column" onPointerDown={onGrab}>
            <Icon name="menu" size={14} />
          </button>
        ) : null}
      </div>
      <div className="tk-card-title" data-testid="card-text">
        {card.text}
      </div>
      {sources.length ? (
        <div className="bd-sources">
          {sources.map((s, i) => {
            // A source the reader may not open (hidden) is not a link.
            const href = sourceHref(s);
            if (href === null) return <span key={i} className="doc-chip doc-chip-off">not available</span>;
            return (
              <AppLink key={i} href={href} title={s.kind === "line" ? (s.quote ?? undefined) : undefined}>
                {s.kind === "line" && !s.line ? "Meeting" : sourceLabel(s)}
              </AppLink>
            );
          })}
        </div>
      ) : null}
      {card.owner ? (
        <div className="tk-card-foot">
          <span className="bd-owner">{card.owner}</span>
        </div>
      ) : null}
      {canEdit ? (
        <div className="bd-tools">
          <select
            className="select select-sm"
            aria-label={`Move “${card.text}” to column`}
            value={to}
            disabled={busy}
            onChange={(event) => setTo(event.target.value)}
          >
            <option value="">Move to column…</option>
            {columns.map((c, i) =>
              i === column ? null : (
                <option key={i} value={i}>
                  {c.title}
                </option>
              ),
            )}
          </select>
          {to !== "" ? (
            <button
              type="button"
              className="rh-btn"
              disabled={busy}
              onClick={() => {
                const target = Number(to);
                setTo("");
                if (Number.isInteger(target)) onMove(target);
              }}
            >
              Move
            </button>
          ) : null}
          <button type="button" className="rh-btn" aria-label={`Edit card: ${card.text}`} disabled={busy} onClick={onEdit}>
            Edit
          </button>
        </div>
      ) : null}
    </div>
  );
}

function NameForm({
  label,
  submit,
  initial = "",
  busy,
  onSubmit,
  onCancel,
}: {
  label: string;
  submit: string;
  initial?: string;
  busy: boolean;
  onSubmit: (title: string) => void | Promise<void>;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const clean = value.trim();
  return (
    <form
      className="bd-name"
      onSubmit={(event) => {
        event.preventDefault();
        if (clean) void onSubmit(clean);
      }}
    >
      <input
        className="input"
        aria-label={label}
        value={value}
        maxLength={100}
        autoFocus
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
        }}
      />
      <Button kind="primary" size="sm" type="submit" disabled={busy || !clean}>
        {submit}
      </Button>
      <Button kind="secondary" size="sm" type="button" onClick={onCancel}>
        Cancel
      </Button>
    </form>
  );
}

function CardForm({
  initial,
  submit,
  busy,
  onSubmit,
  onCancel,
  onDelete,
}: {
  initial?: BoardCard;
  submit: string;
  busy: boolean;
  onSubmit: (fields: CardFields) => void | Promise<void>;
  onCancel: () => void;
  onDelete?: () => void | Promise<void>;
}) {
  const [text, setText] = useState(initial?.text ?? "");
  const [owner, setOwner] = useState(initial?.owner ?? "");
  const [kind, setKind] = useState<CardKind>(initial?.kind ?? "note");
  const clean = text.trim();
  return (
    <form
      className="tk-card bd-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (clean) void onSubmit({ text: clean, owner: owner.trim(), kind });
      }}
    >
      <label className="bd-label">
        Card text
        <textarea className="textarea" rows={3} maxLength={300} value={text} autoFocus onChange={(event) => setText(event.target.value)} />
      </label>
      <label className="bd-label">
        Owner
        <input className="input" maxLength={80} value={owner} onChange={(event) => setOwner(event.target.value)} />
      </label>
      <label className="bd-label">
        Kind
        <select className="select select-sm" value={kind} onChange={(event) => setKind(event.target.value as CardKind)}>
          {KINDS.map((k) => (
            <option key={k.kind} value={k.kind}>
              {k.label}
            </option>
          ))}
        </select>
      </label>
      <div className="bd-form-actions">
        <Button kind="primary" size="sm" type="submit" disabled={busy || !clean}>
          {submit}
        </Button>
        <Button kind="secondary" size="sm" type="button" onClick={onCancel}>
          Cancel
        </Button>
        {onDelete ? (
          <Button kind="danger" size="sm" type="button" disabled={busy} onClick={() => void onDelete()}>
            Delete card
          </Button>
        ) : null}
      </div>
    </form>
  );
}
