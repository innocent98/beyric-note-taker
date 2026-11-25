import { describe, expect, it } from "vitest";
import type { BoardColumn } from "@/lib/board";
import { boardFilename, boardPng, drawBoard, layoutBoard, pngSize, type Palette } from "@/lib/board-png";

// The board's PNG is built in the browser with a canvas (no server work). jsdom has no canvas, so
// the drawing is split in three pieces that need none: the layout (pure, measured by a function),
// the drawing (calls on a context, recorded here) and the file (a canvas made by `createCanvas`).
// That the real browser's file has the board's size is checked in the browser pass of the SOP.

const measure = (text: string) => text.length * 7; // a 7 px wide letter
const card = (text: string, kind: BoardColumn["cards"][number]["kind"] = "note", owner: string | null = null) => ({
  text,
  kind,
  owner,
  sources: [],
});
const column = (title: string, ...cards: ReturnType<typeof card>[]): BoardColumn => ({ type: "column", title, cards });
const PALETTE: Palette = {
  background: "bg",
  surface: "surface",
  column: "column",
  text: "text",
  muted: "muted",
  border: "border",
  kind: { decision: "k-decision", action: "k-action", question: "k-question", note: "k-note" },
  font: "sans",
};
const sample = () => [
  column("Pricing", card("Onboarding is 50,000 naira", "decision"), card("Who signs?", "question")),
  column("Next steps", card("Send the plan", "action", "Ada")),
  column("Later"),
];

describe("layout", () => {
  it("is as wide as its columns and as tall as the tallest", () => {
    const layout = layoutBoard("Onboarding board", sample(), measure);
    expect(layout.columns).toHaveLength(3);
    expect(layout.width).toBe(layout.columns[2].x + layout.columns[2].width + layout.margin);
    const tallest = Math.max(...layout.columns.map((c) => c.y + c.height));
    expect(layout.height).toBe(tallest + layout.margin);
    // every column is drawn as high as the tallest, so the picture is a clean rectangle
    expect(new Set(layout.columns.map((c) => c.height)).size).toBe(1);
  });

  it("puts every card inside its column, one under the other, never overlapping", () => {
    const layout = layoutBoard("B", sample(), measure);
    for (const col of layout.columns) {
      let bottom = col.y;
      for (const c of col.cards) {
        expect(c.x).toBeGreaterThanOrEqual(col.x);
        expect(c.x + c.width).toBeLessThanOrEqual(col.x + col.width);
        expect(c.y).toBeGreaterThanOrEqual(bottom);
        bottom = c.y + c.height;
      }
      expect(bottom).toBeLessThanOrEqual(col.y + col.height);
    }
  });

  it("wraps a long card at the column's width and breaks a word that is longer than it", () => {
    const long = "word ".repeat(40) + "x".repeat(200);
    const layout = layoutBoard("B", [column("A", card(long), card("short"))], measure);
    const [wide, short] = layout.columns[0].cards;
    expect(wide.lines.length).toBeGreaterThan(5);
    for (const line of wide.lines) expect(measure(line)).toBeLessThanOrEqual(wide.width - 2 * layout.cardPad);
    expect(wide.lines.join(" ").replace(/\s+/g, "")).toBe(long.replace(/\s+/g, ""));
    expect(wide.height).toBeGreaterThan(short.height);
  });

  it("makes a card with an owner taller by its owner line, and keeps the kind and owner", () => {
    const layout = layoutBoard("B", [column("A", card("x", "action", "Ada"), card("x", "action"))], measure);
    const [withOwner, without] = layout.columns[0].cards;
    expect(withOwner.height).toBeGreaterThan(without.height);
    expect(withOwner.owner).toBe("Ada");
    expect(withOwner.kind).toBe("action");
  });

  it("has a title line and counts of cards in the column headings", () => {
    const layout = layoutBoard("Onboarding board", sample(), measure);
    expect(layout.title.lines).toEqual(["Onboarding board"]);
    expect(layout.columns.map((c) => [c.title, c.count])).toEqual([
      ["Pricing", 2],
      ["Next steps", 1],
      ["Later", 0],
    ]);
  });

  it("is still a picture for a board with no columns", () => {
    const layout = layoutBoard("Empty", [], measure);
    expect(layout.width).toBeGreaterThan(0);
    expect(layout.height).toBeGreaterThan(0);
  });
});

class Recorder {
  calls: [string, ...unknown[]][] = [];
  fillStyle = "";
  strokeStyle = "";
  font = "";
  textBaseline = "";
  lineWidth = 0;
  fills: string[] = [];
  fill() {
    this.fills.push(this.fillStyle);
  }
  record(name: string) {
    return (...args: unknown[]) => {
      if (name === "fillRect") this.fills.push(this.fillStyle);
      this.calls.push([name, ...args]);
    };
  }
  fillRect = this.record("fillRect");
  strokeRect = this.record("strokeRect");
  fillText = this.record("fillText");
  beginPath = this.record("beginPath");
  roundRect = this.record("roundRect");
  stroke = this.record("stroke");
  scale = this.record("scale");
  measureText = (text: string) => ({ width: measure(text) });
}

describe("drawing", () => {
  it("writes the title, the column names, every card and every owner, in the palette's colours", () => {
    const layout = layoutBoard("Onboarding board", sample(), measure);
    const ctx = new Recorder();
    drawBoard(ctx as unknown as CanvasRenderingContext2D, layout, PALETTE);
    const written = ctx.calls.filter(([n]) => n === "fillText").map(([, t]) => t);
    for (const text of ["Onboarding board", "Pricing", "Next steps", "Later", "Onboarding is 50,000 naira", "Who signs?", "Send the plan", "Ada"]) {
      expect(written.some((w) => String(w).includes(text)), text).toBe(true);
    }
    // a stripe for each kind that is on the board
    expect(ctx.fills).toEqual(expect.arrayContaining(["bg", "k-decision", "k-question", "k-action"]));
    expect(ctx.fills).not.toContain("k-note");
  });

  it("draws text as it is, never as markup", () => {
    const ctx = new Recorder();
    const layout = layoutBoard("<b>t</b>", [column("<i>A</i>", card("<script>x</script>"))], measure);
    drawBoard(ctx as unknown as CanvasRenderingContext2D, layout, PALETTE);
    const written = ctx.calls.filter(([n]) => n === "fillText").map(([, t]) => t);
    expect(written).toContain("<script>x</script>");
  });
});

// A PNG's first bytes: the signature, then the IHDR chunk with the width and the height.
function pngHeader(width: number, height: number): Uint8Array<ArrayBuffer> {
  const bytes = new Uint8Array(new ArrayBuffer(24));
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52]);
  const view = new DataView(bytes.buffer);
  view.setUint32(16, width);
  view.setUint32(20, height);
  return bytes;
}

describe("the file", () => {
  it("reads the size of a PNG from its header, and says nothing of anything else", () => {
    expect(pngSize(pngHeader(1234, 567))).toEqual({ width: 1234, height: 567 });
    expect(pngSize(new Uint8Array([1, 2, 3]))).toBeNull();
    expect(pngSize(new Uint8Array(24))).toBeNull();
  });

  function fakeCanvas() {
    const ctx = new Recorder();
    const canvas = {
      width: 0,
      height: 0,
      getContext: () => ctx,
      toBlob: (done: (blob: Blob | null) => void) =>
        done(new Blob([pngHeader(canvas.width, canvas.height)], { type: "image/png" })),
    };
    return { canvas, ctx };
  }

  it("is a canvas as big as the board times the scale, and a PNG of that size", async () => {
    const layout = layoutBoard("Onboarding board", sample(), measure);
    const { canvas, ctx } = fakeCanvas();
    const blob = await boardPng("Onboarding board", sample(), {
      scale: 2,
      palette: PALETTE,
      createCanvas: () => canvas as unknown as HTMLCanvasElement,
      measure,
    });
    expect(canvas.width).toBe(layout.width * 2);
    expect(canvas.height).toBe(layout.height * 2);
    expect(ctx.calls.some(([n, x, y]) => n === "scale" && x === 2 && y === 2)).toBe(true);
    expect(blob.type).toBe("image/png");
    const size = pngSize(new Uint8Array(await blob.arrayBuffer()));
    expect(size).toEqual({ width: layout.width * 2, height: layout.height * 2 });
  });

  it("is made smaller than asked when the board is huge, so the browser can draw it", async () => {
    const many = Array.from({ length: 12 }, (_, i) => column(`T${i}`, ...Array.from({ length: 30 }, () => card("x".repeat(120)))));
    const { canvas } = fakeCanvas();
    await boardPng("Big", many, { scale: 3, palette: PALETTE, createCanvas: () => canvas as unknown as HTMLCanvasElement, measure });
    expect(canvas.width).toBeLessThanOrEqual(8192);
    expect(canvas.height).toBeLessThanOrEqual(8192);
    expect(canvas.width).toBeGreaterThan(0);
  });

  it("fails plainly when the browser gives no canvas or no file", async () => {
    const none = { width: 0, height: 0, getContext: () => null, toBlob: () => {} };
    await expect(
      boardPng("B", sample(), { palette: PALETTE, createCanvas: () => none as unknown as HTMLCanvasElement, measure }),
    ).rejects.toThrow(/canvas/i);
    const noFile = { ...fakeCanvas().canvas, toBlob: (done: (b: Blob | null) => void) => done(null) };
    await expect(
      boardPng("B", sample(), { palette: PALETTE, createCanvas: () => noFile as unknown as HTMLCanvasElement, measure }),
    ).rejects.toThrow(/png/i);
  });

  it("is named after the board, safe as a file name", () => {
    expect(boardFilename("Onboarding board")).toBe("Onboarding board.png");
    expect(boardFilename('a/b\\c:d*e?"f<g>h|i')).toBe("abcdefghi.png");
    expect(boardFilename("   ")).toBe("board.png");
    expect(boardFilename("x".repeat(300)).length).toBeLessThanOrEqual(104);
  });
});
