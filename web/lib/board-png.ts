import type { BoardColumn, CardKind } from "./board";
import { KIND_LABEL } from "./board";

// The board as a PNG, built IN THE BROWSER with a canvas: no request, no server work, and the file
// exists only for the click (`saveBlob`). The picture is the same board the screen shows: a title,
// then the columns side by side, each as high as the tallest, with its cards (a stripe in the
// colour of the card's kind, the kind in words, the text, the owner).
//
// Three pieces, so none but the last needs a real canvas: `layoutBoard` (pure: where everything
// goes, given a function that measures text), `drawBoard` (calls on a 2d context) and `boardPng`
// (makes the canvas and the file). Colours are read from the page's CSS variables
// (`paletteFromCss`): this file holds no colour of its own.

export type Palette = {
  background: string;
  surface: string; // a card
  column: string; // a column's background
  text: string;
  muted: string;
  border: string;
  kind: Record<CardKind, string>;
  font: string; // the font family
};

export type Measure = (text: string, bold?: boolean) => number;

const MARGIN = 24;
const TITLE_SIZE = 22;
const TITLE_LINE = 30;
const COLUMN_WIDTH = 260;
const GAP = 16;
const COLUMN_PAD = 10;
const HEAD_HEIGHT = 34;
const CARD_PAD = 12;
const CARD_GAP = 10;
const TEXT_SIZE = 14;
const TEXT_LINE = 20;
const SMALL_SIZE = 12;
const SMALL_LINE = 16;
const STRIPE = 4;
const RADIUS = 8;
const MAX_SIDE = 8192; // what a browser's canvas reliably holds

export type CardShape = {
  x: number;
  y: number;
  width: number;
  height: number;
  kind: CardKind;
  lines: string[];
  owner: string | null;
};
export type ColumnShape = {
  x: number;
  y: number;
  width: number;
  height: number;
  title: string;
  count: number;
  cards: CardShape[];
};
export type Layout = {
  width: number;
  height: number;
  margin: number;
  cardPad: number;
  title: { x: number; y: number; lines: string[] };
  columns: ColumnShape[];
};

// `text` broken into lines no wider than `width`: at spaces, and inside a word that is wider than a
// line by itself.
function wrap(text: string, width: number, measure: Measure): string[] {
  const lines: string[] = [];
  let line = "";
  const push = () => {
    if (line) lines.push(line);
    line = "";
  };
  for (const word of text.split(/\s+/).filter(Boolean)) {
    const joined = line ? `${line} ${word}` : word;
    if (measure(joined) <= width) {
      line = joined;
      continue;
    }
    push();
    let rest = word;
    while (measure(rest) > width) {
      let cut = rest.length - 1;
      while (cut > 1 && measure(rest.slice(0, cut)) > width) cut--;
      lines.push(rest.slice(0, cut));
      rest = rest.slice(cut);
    }
    line = rest;
  }
  push();
  return lines.length ? lines : [""];
}

export function layoutBoard(title: string, columns: BoardColumn[], measure: Measure): Layout {
  const inner = columns.length * COLUMN_WIDTH + Math.max(0, columns.length - 1) * GAP;
  const width = MARGIN * 2 + Math.max(inner, 320);
  const titleLines = wrap(title || "Board", width - 2 * MARGIN, measure);
  const top = MARGIN + titleLines.length * TITLE_LINE + MARGIN / 2;
  const cardWidth = COLUMN_WIDTH - 2 * COLUMN_PAD;
  const textWidth = cardWidth - 2 * CARD_PAD - STRIPE;
  const shaped = columns.map((column, c) => {
    const x = MARGIN + c * (COLUMN_WIDTH + GAP);
    let y = top + COLUMN_PAD + HEAD_HEIGHT;
    const cards = column.cards.map((card): CardShape => {
      const lines = wrap(card.text, textWidth, measure);
      const height =
        CARD_PAD * 2 + SMALL_LINE + lines.length * TEXT_LINE + (card.owner ? SMALL_LINE + 4 : 0);
      const shape = {
        x: x + COLUMN_PAD,
        y,
        width: cardWidth,
        height,
        kind: card.kind,
        lines,
        owner: card.owner ?? null,
      };
      y += height + CARD_GAP;
      return shape;
    });
    return { x, y: top, title: column.title, count: column.cards.length, cards, bottom: y };
  });
  const tallest = Math.max(
    top + COLUMN_PAD * 2 + HEAD_HEIGHT + 60,
    ...shaped.map((s) => (s.cards.length ? s.bottom - CARD_GAP + COLUMN_PAD : s.bottom + 40)),
  );
  return {
    width,
    height: tallest + MARGIN,
    margin: MARGIN,
    cardPad: CARD_PAD,
    title: { x: MARGIN, y: MARGIN, lines: titleLines },
    columns: shaped.map((s) => ({
      x: s.x,
      y: s.y,
      title: s.title,
      count: s.count,
      cards: s.cards,
      width: COLUMN_WIDTH,
      height: tallest - top,
    })),
  };
}

function round(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, fill: string, line?: string) {
  ctx.fillStyle = fill;
  if (typeof ctx.roundRect === "function") {
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, RADIUS);
    ctx.fill();
  } else {
    ctx.fillRect(x, y, w, h);
  }
  if (line) {
    ctx.strokeStyle = line;
    ctx.lineWidth = 1;
    ctx.stroke();
  }
}

export function drawBoard(ctx: CanvasRenderingContext2D, layout: Layout, palette: Palette): void {
  ctx.fillStyle = palette.background;
  ctx.fillRect(0, 0, layout.width, layout.height);
  ctx.textBaseline = "top";
  ctx.fillStyle = palette.text;
  ctx.font = `700 ${TITLE_SIZE}px ${palette.font}`;
  layout.title.lines.forEach((line, i) => ctx.fillText(line, layout.title.x, layout.title.y + i * TITLE_LINE));
  for (const column of layout.columns) {
    round(ctx, column.x, column.y, column.width, column.height, palette.column);
    ctx.fillStyle = palette.text;
    ctx.font = `600 ${TEXT_SIZE}px ${palette.font}`;
    ctx.fillText(column.title, column.x + COLUMN_PAD + 4, column.y + COLUMN_PAD + 8);
    ctx.fillStyle = palette.muted;
    ctx.font = `500 ${SMALL_SIZE}px ${palette.font}`;
    ctx.fillText(String(column.count), column.x + column.width - COLUMN_PAD - 14, column.y + COLUMN_PAD + 9);
    for (const card of column.cards) {
      round(ctx, card.x, card.y, card.width, card.height, palette.surface, palette.border);
      ctx.fillStyle = palette.kind[card.kind];
      ctx.fillRect(card.x, card.y + 6, STRIPE, card.height - 12);
      const left = card.x + CARD_PAD + STRIPE;
      let y = card.y + CARD_PAD;
      ctx.fillStyle = palette.muted;
      ctx.font = `600 ${SMALL_SIZE}px ${palette.font}`;
      ctx.fillText(KIND_LABEL[card.kind].toUpperCase(), left, y);
      y += SMALL_LINE;
      ctx.fillStyle = palette.text;
      ctx.font = `400 ${TEXT_SIZE}px ${palette.font}`;
      for (const line of card.lines) {
        ctx.fillText(line, left, y);
        y += TEXT_LINE;
      }
      if (card.owner) {
        ctx.fillStyle = palette.muted;
        ctx.font = `500 ${SMALL_SIZE}px ${palette.font}`;
        ctx.fillText(card.owner, left, y + 4);
      }
    }
  }
}

function cssVar(style: CSSStyleDeclaration, name: string): string {
  return style.getPropertyValue(name).trim();
}

// The page's own colours (styles/tokens.css) for the picture: a card is `--surface`, a column
// `--surface-active`, and a kind has the colour its chip has on screen.
export function paletteFromCss(root: HTMLElement = document.documentElement): Palette {
  const style = getComputedStyle(root);
  return {
    background: cssVar(style, "--bg"),
    surface: cssVar(style, "--surface"),
    column: cssVar(style, "--surface-active"),
    text: cssVar(style, "--text"),
    muted: cssVar(style, "--text-3"),
    border: cssVar(style, "--border"),
    kind: {
      decision: cssVar(style, "--teal"),
      action: cssVar(style, "--dot-amber"),
      question: cssVar(style, "--info"),
      note: cssVar(style, "--chevron"),
    },
    font: getComputedStyle(root).fontFamily || "sans-serif",
  };
}

export type PngOptions = {
  scale?: number; // 2 by default: sharp on a screen with a high pixel density
  palette?: Palette;
  measure?: Measure;
  createCanvas?: () => HTMLCanvasElement;
};

export async function boardPng(title: string, columns: BoardColumn[], options: PngOptions = {}): Promise<Blob> {
  const palette = options.palette ?? paletteFromCss();
  const canvas = options.createCanvas ? options.createCanvas() : document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("This browser cannot draw a canvas.");
  const measure =
    options.measure ??
    ((text: string, bold = false) => {
      ctx.font = `${bold ? 600 : 400} ${TEXT_SIZE}px ${palette.font}`;
      return ctx.measureText(text).width;
    });
  const layout = layoutBoard(title, columns, measure);
  const scale = Math.max(1, Math.min(options.scale ?? 2, MAX_SIDE / layout.width, MAX_SIDE / layout.height));
  canvas.width = Math.floor(layout.width * scale);
  canvas.height = Math.floor(layout.height * scale);
  ctx.scale(scale, scale);
  drawBoard(ctx, layout, palette);
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("The PNG could not be made."))), "image/png");
  });
}

// The width and height written in a PNG's header, or null for anything that is not a PNG. Used to
// check that the file has the board's size.
export function pngSize(bytes: Uint8Array): { width: number; height: number } | null {
  const signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  if (bytes.length < 24 || signature.some((b, i) => bytes[i] !== b)) return null;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return { width: view.getUint32(16), height: view.getUint32(20) };
}

const NAME_MAX = 100;

export function boardFilename(title: string): string {
  const cleaned = title
    .replace(/[\\/:*?"<>|]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, NAME_MAX)
    .trim();
  return `${cleaned || "board"}.png`;
}
