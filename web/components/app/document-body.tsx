"use client";

import { BoardView } from "@/components/app/board-view";
import { DocumentEditor } from "@/components/app/document-editor";
import { Marked, SourceChips } from "@/components/app/document-parts";
import type { DocumentBlock, DocumentDraft, DocumentItem } from "@/lib/api/types";
import { sourcesOf } from "@/lib/documents";

function Item({ item, flagged, available }: { item: DocumentItem; flagged: string[]; available: boolean }) {
  return (
    <li>
      <Marked text={item.text} flagged={flagged} />
      <SourceChips sources={item.sources ?? []} available={available} />
    </li>
  );
}

function ReadBlock({ block, flagged, available }: { block: DocumentBlock; flagged: string[]; available: boolean }) {
  switch (block.type) {
    case "heading":
      return block.level === 3 ? <h3 className="doc-h3">{block.text}</h3> : <h2 className="doc-h2">{block.text}</h2>;
    case "paragraph":
      return (
        <div className="doc-block">
          <p className="doc-p">
            <Marked text={block.text} flagged={flagged} />
          </p>
          <SourceChips sources={sourcesOf(block)} available={available} />
        </div>
      );
    case "bullets":
      return (
        <ul className="doc-list">
          {block.items.map((item, i) => (
            <Item key={i} item={item} flagged={flagged} available={available} />
          ))}
        </ul>
      );
    case "table":
      return (
        <div className="doc-block">
          <div className="doc-table-wrap">
            <table className="doc-table">
              <thead>
                <tr>
                  {block.columns.map((name, i) => (
                    <th key={i} scope="col">
                      {name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.map((row, r) => (
                  <tr key={r}>
                    {row.map((cell, c) => (
                      <td key={c}>
                        <Marked text={cell.text} flagged={flagged} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <SourceChips sources={sourcesOf(block)} available={available} />
        </div>
      );
    default:
      return null; // a board's column: the board screen draws it
  }
}

// What the document page shows under its header, by output type. A document, an email and a table are
// blocks, read here or edited in `DocumentEditor`. A board is its own screen (`BoardView`, slice S6): it
// edits in place through PATCH /api/documents/{id}/board, so the page's block editor never opens for it.
// `canEdit` is false for a reader of a shared board (only its creator changes it); `onSaved` takes the
// whole draft each change answers and `onStale` asks the page to fetch it again.
export function DocumentBody({
  doc,
  blocks,
  editing,
  onBlocks,
  canEdit = true,
  onSaved = () => {},
  onStale = () => {},
}: {
  doc: DocumentDraft;
  blocks: DocumentBlock[];
  editing: boolean;
  onBlocks: (next: DocumentBlock[]) => void;
  canEdit?: boolean;
  onSaved?: (next: DocumentDraft) => void;
  onStale?: () => void | Promise<void>;
}) {
  if (doc.output_type === "board") return <BoardView doc={doc} canEdit={canEdit} onSaved={onSaved} onStale={onStale} />;
  if (editing) return <DocumentEditor blocks={blocks} onChange={onBlocks} available={doc.sources_available} />;
  const flags = doc.checks.flags;
  return (
    <div className="doc-body">
      {blocks.map((block, index) => (
        <ReadBlock
          key={index}
          block={block}
          flagged={flags.filter((f) => f.block_index === index).map((f) => f.text)}
          available={doc.sources_available}
        />
      ))}
    </div>
  );
}
