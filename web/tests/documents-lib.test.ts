import { describe, expect, it } from "vitest";
import type { DocumentBlock, DocumentDraft } from "@/lib/api/types";
import {
  MAILTO_LIMIT,
  blocksToText,
  countBlanks,
  highlightRuns,
  insertAt,
  mailtoHref,
  moveBy,
  newBlock,
  removeAt,
  sourceHref,
  sourceLabel,
  sourcesOf,
} from "@/lib/documents";
import { recipesBody } from "./helpers";

// Fed the bodies the running API answered with (docs/fe-captures/recipes/).
const email = () => recipesBody<DocumentDraft>("11-get-ready.json");
const proposal = () => recipesBody<DocumentDraft>("exports/24-the-draft.json");

describe("plain text for Copy", () => {
  it("is the very text the server's plain-text export holds", () => {
    const exported = recipesBody<string>("exports/07-text.json");
    expect(blocksToText(proposal().blocks)).toBe(exported);
  });

  it("writes a board's column as its title and cards", () => {
    const board: DocumentBlock[] = [
      { type: "column", title: "Decisions", cards: [{ text: "Ship it", kind: "decision", owner: "Ada", sources: [] }, { text: "Plan", kind: "note", sources: [] }] },
    ];
    expect(blocksToText(board)).toBe("Decisions\n- Ship it (Ada)\n- Plan");
  });
});

describe("sources", () => {
  it("links a line to the transcript at that row and anything else to the meeting", () => {
    const [, hi] = email().blocks;
    const source = sourcesOf(hi)[0];
    expect(sourceHref(source)).toBe("/meetings/1?tab=transcript&line=1");
    expect(sourceLabel(source)).toBe("Line 1");
    expect(sourceHref({ kind: "meeting", meeting_id: 7 })).toBe("/meetings/7");
    expect(sourceHref({ kind: "decision", meeting_id: 7, ref: 3 })).toBe("/meetings/7");
    expect(sourceLabel({ kind: "task", meeting_id: 7, ref: 3 })).toBe("Task");
  });

  it("collects a block's sources once each, from its paragraph, items or cells", () => {
    const blocks = email().blocks;
    expect(sourcesOf(blocks[3]).map((s) => s.line)).toEqual([3]);
    const table: DocumentBlock = {
      type: "table",
      columns: ["A"],
      rows: [[{ text: "x", sources: [{ kind: "line", meeting_id: 1, line: 2 }] }], [{ text: "y", sources: [{ kind: "line", meeting_id: 1, line: 2 }] }]],
    };
    expect(sourcesOf(table)).toHaveLength(1);
  });
});

describe("blanks and flagged figures", () => {
  it("marks a [need: ...] token and a flagged figure, and nothing else", () => {
    const text = email().blocks[2] as { text: string };
    const runs = highlightRuns(text.text, ["120,000 naira"]);
    expect(runs.filter((r) => r.kind !== "plain").map((r) => [r.kind, r.text])).toEqual([
      ["check", "120,000 naira"],
      ["blank", "[need: call date]"],
    ]);
    expect(runs.map((r) => r.text).join("")).toBe(text.text);
  });

  it("marks every place a figure occurs, and a figure inside a blank stays a blank", () => {
    const runs = highlightRuns("50 and 50 and [need: 50 units]", ["50"]);
    expect(runs.map((r) => r.kind)).toEqual(["check", "plain", "check", "plain", "blank"]);
  });

  it("counts the blanks of the blocks on screen", () => {
    expect(countBlanks(email().blocks)).toBe(1);
    expect(countBlanks(proposal().blocks)).toBe(2);
  });
});

describe("the mail app", () => {
  it("takes a leading Subject paragraph as the subject and leaves it out of the body", () => {
    const href = mailtoHref("Thanks for today", email().blocks) as string;
    const query = new URLSearchParams(href.slice("mailto:?".length));
    expect(href.startsWith("mailto:?subject=")).toBe(true);
    expect(query.get("subject")).toBe("Thanks for today, next steps on the onboarding");
    expect(query.get("body")).toBe(blocksToText(email().blocks.slice(1)));
    expect(query.get("body")).not.toContain("Subject:");
  });

  it("uses the title when there is no Subject line", () => {
    const query = new URLSearchParams((mailtoHref("My title", proposal().blocks) as string).slice("mailto:?".length));
    expect(query.get("subject")).toBe("My title");
  });

  it("gives no link when it would be too long", () => {
    const long: DocumentBlock[] = [{ type: "paragraph", text: "word ".repeat(MAILTO_LIMIT), sources: [] }];
    expect(mailtoHref("t", long)).toBeNull();
  });
});

describe("list edits", () => {
  it("inserts, removes and moves without changing the list given", () => {
    const list = ["a", "b", "c"];
    expect(insertAt(list, 1, "x")).toEqual(["a", "x", "b", "c"]);
    expect(removeAt(list, 0)).toEqual(["b", "c"]);
    expect(moveBy(list, 1, -1)).toEqual(["b", "a", "c"]);
    expect(moveBy(list, 2, 1)).toBe(list);
    expect(moveBy(list, 0, -1)).toBe(list);
    expect(list).toEqual(["a", "b", "c"]);
  });

  it("makes new blocks with no sources", () => {
    expect(newBlock("paragraph")).toEqual({ type: "paragraph", text: "", sources: [] });
    const table = newBlock("table");
    expect(table.type === "table" && table.columns.length === 2 && table.rows.length === 1).toBe(true);
    expect(newBlock("heading")).toEqual({ type: "heading", level: 2, text: "" });
  });
});
