import type { DocumentBlock, DocumentDraft, DocumentItem, DocumentSource } from "@/lib/api/types";

// Helpers behind the document screen (components/app/document-view.tsx, document-editor.tsx): what a
// source chip says and links to, the `[need: ...]` blanks, the plain text of a draft for Copy and the
// mail app, and the edits the block editor makes. The server is the authority for the saved text
// (PATCH cleans and bounds it); these only shape what is on the screen.

// ---- sources ----

// A line source opens the transcript at that row (`?tab=transcript&line=N`, the mechanism Ask Beyric
// uses); the meeting page shows the plain transcript when the row does not exist, so a line that moved
// degrades to the meeting. A meeting, decision or task source opens the meeting.
export function sourceHref(source: DocumentSource): string {
  const base = `/meetings/${source.meeting_id}`;
  if (source.kind === "line" && source.line != null && source.line >= 1) return `${base}?tab=transcript&line=${source.line}`;
  return base;
}

export function sourceLabel(source: DocumentSource): string {
  if (source.kind === "line") return source.line != null ? `Line ${source.line}` : "Transcript";
  if (source.kind === "decision") return "Decision";
  if (source.kind === "task") return "Task";
  return "Meeting";
}

// The first words of the line, when the server kept them: what the chip's tooltip shows.
export function sourceHint(source: DocumentSource): string | undefined {
  return source.quote ? `"${source.quote}"` : undefined;
}

// Every source of a block, in order, once each (a table's come from its cells, a list's from its items).
export function sourcesOf(block: DocumentBlock): DocumentSource[] {
  const found: DocumentSource[] = [];
  const add = (sources: DocumentSource[] | undefined) => {
    for (const source of sources ?? []) {
      const same = found.some(
        (s) => s.kind === source.kind && s.meeting_id === source.meeting_id && s.line === source.line && s.ref === source.ref,
      );
      if (!same) found.push(source);
    }
  };
  if (block.type === "paragraph") add(block.sources);
  else if (block.type === "bullets") block.items.forEach((item) => add(item.sources));
  else if (block.type === "table") block.rows.forEach((row) => row.forEach((cell) => add(cell.sources)));
  else if (block.type === "column") block.cards.forEach((card) => add(card.sources));
  return found;
}

// ---- blanks ----

const BLANK = /\[need:[^\]]*\]/g;

export type Run = { text: string; kind: "plain" | "blank" | "check" };

// The text split so a blank (`[need: ...]`) and a figure the server flagged can be drawn marked, in
// order. `flagged` are the texts of `checks.flags` for this block ("120,000 naira"); every place one
// occurs is marked "check", and a flag inside a blank stays a blank.
export function highlightRuns(text: string, flagged: string[] = []): Run[] {
  const marks: { start: number; end: number; kind: "blank" | "check" }[] = [];
  for (const match of text.matchAll(BLANK)) {
    marks.push({ start: match.index ?? 0, end: (match.index ?? 0) + match[0].length, kind: "blank" });
  }
  for (const figure of new Set(flagged.filter(Boolean))) {
    for (let at = text.indexOf(figure); at !== -1; at = text.indexOf(figure, at + figure.length)) {
      marks.push({ start: at, end: at + figure.length, kind: "check" });
    }
  }
  marks.sort((a, b) => a.start - b.start || b.end - a.end);
  const runs: Run[] = [];
  let last = 0;
  for (const mark of marks) {
    if (mark.start < last) continue; // inside the one before
    if (mark.start > last) runs.push({ text: text.slice(last, mark.start), kind: "plain" });
    runs.push({ text: text.slice(mark.start, mark.end), kind: mark.kind });
    last = mark.end;
  }
  if (last < text.length) runs.push({ text: text.slice(last), kind: "plain" });
  return runs;
}

// Blanks and flagged figures as the person is told: "1 blank to fill", "2 figures to check".
export function checkCounts(checks: DocumentDraft["checks"]): { blanks: number; figures: number } {
  return { blanks: checks.blanks.length, figures: checks.flags.length };
}

export const blanksPhrase = (n: number) => `${n} ${n === 1 ? "blank" : "blanks"} to fill`;
export const figuresPhrase = (n: number) => `${n} ${n === 1 ? "figure" : "figures"} to check`;

// Count the `[need: ...]` tokens in blocks as they stand on screen (the editor's working copy; the server
// recounts from the saved blocks).
export function countBlanks(blocks: DocumentBlock[]): number {
  return textsOf(blocks).reduce((n, text) => n + (text.match(BLANK)?.length ?? 0), 0);
}

function textsOf(blocks: DocumentBlock[]): string[] {
  const out: string[] = [];
  for (const block of blocks) {
    if (block.type === "heading" || block.type === "paragraph") out.push(block.text);
    else if (block.type === "bullets") block.items.forEach((i) => out.push(i.text));
    else if (block.type === "table") {
      out.push(...block.columns);
      block.rows.forEach((row) => row.forEach((c) => out.push(c.text)));
    } else {
      out.push(block.title);
      block.cards.forEach((c) => out.push(c.text));
    }
  }
  return out;
}

// ---- plain text and the mail app ----

// The draft as plain text, as the server's `to_text` writes it (`GET .../export?format=txt`): bullets as
// "- ", a table as lines joined by " | ", paragraphs separated by a blank line. What Copy puts on the
// clipboard; tests/document-view.test.tsx holds it equal to the server's own file.
export function blocksToText(blocks: DocumentBlock[]): string {
  const parts: string[] = [];
  for (const block of blocks) {
    if (block.type === "heading" || block.type === "paragraph") parts.push(block.text);
    else if (block.type === "bullets") parts.push(block.items.map((i) => `- ${i.text}`).join("\n"));
    else if (block.type === "table") {
      parts.push([block.columns.join(" | "), ...block.rows.map((row) => row.map((c) => c.text).join(" | "))].join("\n"));
    } else {
      parts.push(
        [block.title, ...block.cards.map((c) => `- ${c.owner ? `${c.text} (${c.owner})` : c.text}`)].join("\n"),
      );
    }
  }
  return parts.join("\n\n");
}

// Some mail apps and operating systems refuse a link longer than about 2,000 characters; past this the
// button falls back to Copy and says so.
export const MAILTO_LIMIT = 1800;

// "Open in your mail app": a `mailto:` link with the subject and body. A leading "Subject: ..." paragraph
// (what the email recipes write first) becomes the subject and leaves the body; otherwise the title is the
// subject. `null` when the link would be too long: use Copy.
export function mailtoHref(title: string, blocks: DocumentBlock[]): string | null {
  let subject = title;
  let rest = blocks;
  const first = blocks[0];
  const match = first?.type === "paragraph" ? /^subject:\s*(.+)$/i.exec(first.text.trim()) : null;
  if (match) {
    subject = match[1].trim();
    rest = blocks.slice(1);
  }
  const href = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(blocksToText(rest))}`;
  return href.length <= MAILTO_LIMIT ? href : null;
}

// ---- editing ----

export type NewBlockKind = "heading" | "paragraph" | "bullets" | "table";

const item = (text = ""): DocumentItem => ({ text, sources: [] });

// A block the person adds: no text and no sources ("a new block has none"). A table starts with two
// columns and one row.
export function newBlock(kind: NewBlockKind): DocumentBlock {
  switch (kind) {
    case "heading":
      return { type: "heading", level: 2, text: "" };
    case "paragraph":
      return { type: "paragraph", text: "", sources: [] };
    case "bullets":
      return { type: "bullets", items: [item()] };
    case "table":
      return { type: "table", columns: ["", ""], rows: [[item(), item()]] };
  }
}

export function insertAt<T>(list: T[], index: number, value: T): T[] {
  return [...list.slice(0, index), value, ...list.slice(index)];
}

export function removeAt<T>(list: T[], index: number): T[] {
  return list.filter((_, i) => i !== index);
}

export function replaceAt<T>(list: T[], index: number, value: T): T[] {
  return list.map((v, i) => (i === index ? value : v));
}

// Move the entry at `from` one place up (-1) or down (+1); the same list when it cannot move.
export function moveBy<T>(list: T[], from: number, step: -1 | 1): T[] {
  const to = from + step;
  if (to < 0 || to >= list.length) return list;
  const copy = [...list];
  [copy[from], copy[to]] = [copy[to], copy[from]];
  return copy;
}

// Blocks the editor can show. A board's columns have their own screen (the board view).
export const isEditable = (block: DocumentBlock): boolean => block.type !== "column";
