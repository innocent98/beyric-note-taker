"use client";

import { SourceChips } from "@/components/app/document-parts";
import { Button, IconButton } from "@/components/ui";
import type { DocumentBlock, DocumentItem } from "@/lib/api/types";
import { insertAt, moveBy, newBlock, removeAt, replaceAt, sourcesOf, type NewBlockKind } from "@/lib/documents";

const KIND_LABEL: Record<string, string> = { heading: "Heading", paragraph: "Paragraph", bullets: "List", table: "Table" };
const ADD: { kind: NewBlockKind; label: string }[] = [
  { kind: "heading", label: "Add heading" },
  { kind: "paragraph", label: "Add paragraph" },
  { kind: "bullets", label: "Add list" },
  { kind: "table", label: "Add table" },
];

const blankItem = (): DocumentItem => ({ text: "", sources: [] });

// The structured block editor: each block's text is edited in place, blocks are added at the end, moved
// up or down and deleted, a table has row and column controls. A block keeps the sources it had (the
// words stay "drafted from"); a new block has none. The editor holds nothing: `blocks` is the working
// copy the page owns, saved with PATCH as a whole.
export function DocumentEditor({
  blocks,
  onChange,
  available,
}: {
  blocks: DocumentBlock[];
  onChange: (next: DocumentBlock[]) => void;
  available: boolean;
}) {
  return (
    <div className="doc-editor">
      {blocks.map((block, index) => {
        const n = index + 1;
        return (
          <section key={index} className="doc-edit-block" aria-label={`Block ${n}: ${KIND_LABEL[block.type] ?? "Block"}`}>
            <div className="doc-edit-bar">
              <strong>{KIND_LABEL[block.type] ?? "Block"}</strong>
              <span className="doc-edit-tools">
                <Button kind="quiet" size="sm" type="button" aria-label={`Move block ${n} up`} disabled={index === 0} onClick={() => onChange(moveBy(blocks, index, -1))}>
                  Up
                </Button>
                <Button kind="quiet" size="sm" type="button" aria-label={`Move block ${n} down`} disabled={index === blocks.length - 1} onClick={() => onChange(moveBy(blocks, index, 1))}>
                  Down
                </Button>
                <IconButton icon="trash" tone="danger" label={`Delete block ${n}`} onClick={() => onChange(removeAt(blocks, index))} />
              </span>
            </div>
            <BlockFields n={n} block={block} onChange={(next) => onChange(replaceAt(blocks, index, next))} />
            <SourceChips sources={sourcesOf(block)} available={available} />
          </section>
        );
      })}
      <div className="doc-add">
        {ADD.map(({ kind, label }) => (
          <Button key={kind} kind="secondary" size="sm" type="button" onClick={() => onChange(insertAt(blocks, blocks.length, newBlock(kind)))}>
            {label}
          </Button>
        ))}
      </div>
    </div>
  );
}

function BlockFields({ n, block, onChange }: { n: number; block: DocumentBlock; onChange: (next: DocumentBlock) => void }) {
  switch (block.type) {
    case "heading":
      return (
        <div className="doc-edit-row">
          <select className="select select-sm doc-level" aria-label={`Block ${n} level`} value={block.level} onChange={(e) => onChange({ ...block, level: Number(e.target.value) })}>
            <option value={1}>Large</option>
            <option value={2}>Medium</option>
            <option value={3}>Small</option>
          </select>
          <input className="input" aria-label={`Block ${n} text`} value={block.text} onChange={(e) => onChange({ ...block, text: e.target.value })} />
        </div>
      );
    case "paragraph":
      return <textarea className="textarea" aria-label={`Block ${n} text`} rows={3} value={block.text} onChange={(e) => onChange({ ...block, text: e.target.value })} />;
    case "bullets":
      return (
        <div className="doc-edit-items">
          {block.items.map((item, i) => (
            <div key={i} className="doc-edit-row">
              <input className="input" aria-label={`Block ${n}, item ${i + 1}`} value={item.text} onChange={(e) => onChange({ ...block, items: replaceAt(block.items, i, { ...item, text: e.target.value }) })} />
              <IconButton icon="close" label={`Remove item ${i + 1} of block ${n}`} disabled={block.items.length === 1} onClick={() => onChange({ ...block, items: removeAt(block.items, i) })} />
            </div>
          ))}
          <div>
            <Button kind="secondary" size="sm" type="button" aria-label={`Add item to block ${n}`} onClick={() => onChange({ ...block, items: [...block.items, blankItem()] })}>
              Add item
            </Button>
          </div>
        </div>
      );
    case "table":
      return <TableFields n={n} block={block} onChange={onChange} />;
    default:
      return null; // a board's column: the board screen edits it
  }
}

function TableFields({ n, block, onChange }: { n: number; block: Extract<DocumentBlock, { type: "table" }>; onChange: (next: DocumentBlock) => void }) {
  const width = block.columns.length;
  return (
    <div className="doc-edit-items">
      <div className="doc-table-wrap">
        <table className="doc-table doc-table-edit">
          <thead>
            <tr>
              {block.columns.map((name, c) => (
                <th key={c} scope="col">
                  <input className="input" aria-label={`Block ${n}, column ${c + 1} heading`} value={name} onChange={(e) => onChange({ ...block, columns: replaceAt(block.columns, c, e.target.value) })} />
                  <IconButton
                    icon="close"
                    label={`Remove column ${c + 1} of block ${n}`}
                    disabled={width === 1}
                    onClick={() => onChange({ ...block, columns: removeAt(block.columns, c), rows: block.rows.map((row) => removeAt(row, c)) })}
                  />
                </th>
              ))}
              <th aria-hidden="true" />
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c}>
                    <input className="input" aria-label={`Block ${n}, row ${r + 1}, column ${c + 1}`} value={cell.text} onChange={(e) => onChange({ ...block, rows: replaceAt(block.rows, r, replaceAt(row, c, { ...cell, text: e.target.value })) })} />
                  </td>
                ))}
                <td>
                  <IconButton icon="close" label={`Remove row ${r + 1} of block ${n}`} onClick={() => onChange({ ...block, rows: removeAt(block.rows, r) })} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="doc-edit-tools">
        <Button kind="secondary" size="sm" type="button" aria-label={`Add row to block ${n}`} onClick={() => onChange({ ...block, rows: [...block.rows, block.columns.map(blankItem)] })}>
          Add row
        </Button>
        <Button kind="secondary" size="sm" type="button" aria-label={`Add column to block ${n}`} onClick={() => onChange({ ...block, columns: [...block.columns, ""], rows: block.rows.map((row) => [...row, blankItem()]) })}>
          Add column
        </Button>
      </div>
    </div>
  );
}
