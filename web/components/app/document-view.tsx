"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { CopyButton } from "@/components/app/copy-button";
import { DocumentBody } from "@/components/app/document-body";
import { DocumentShareDialog } from "@/components/app/document-share-dialog";
import { useDocument } from "@/components/app/use-document";
import { Alert, AppLink, Badge, Button, Field, Icon } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { DocumentBlock, DocumentDraft, DocumentExportFormat } from "@/lib/api/types";
import { saveBlob } from "@/lib/save-file";
import { blanksPhrase, blocksToText, checkCounts, figuresPhrase, mailtoHref, sharedWithPhrase, withoutHiddenSources } from "@/lib/documents";

const KIND_NAME: Record<DocumentDraft["output_type"], string> = { document: "Document", email: "Email", table: "Table", board: "Board" };

type Download = { key: string; format: DocumentExportFormat; table: number; tag: string; label: string; sub: string };

// The Download menu's entries for this draft: Word, PDF and Markdown always; CSV (one per table, named
// from the heading above it) and Excel (every table) only when the draft has a table.
function downloads(blocks: DocumentBlock[]): Download[] {
  const list: Download[] = [
    { key: "docx", format: "docx", table: 0, tag: "DOCX", label: "Word document", sub: "Edit in Word or Google Docs" },
    { key: "pdf", format: "pdf", table: 0, tag: "PDF", label: "PDF", sub: "Best for reading and printing" },
    { key: "md", format: "md", table: 0, tag: "MD", label: "Markdown", sub: "Plain text with simple marks" },
  ];
  const tables: string[] = [];
  let heading: string | null = null;
  for (const block of blocks) {
    if (block.type === "heading") heading = block.text;
    else if (block.type === "table") tables.push(heading && !tables.includes(heading) ? heading : `Table ${tables.length + 1}`);
  }
  if (tables.length > 0) {
    tables.forEach((name, table) => list.push({ key: `csv${table}`, format: "csv", table, tag: "CSV", label: `CSV, ${name}`, sub: "For a spreadsheet or another app" }));
    list.push({ key: "xlsx", format: "xlsx", table: 0, tag: "XLSX", label: "Excel", sub: tables.length > 1 ? "Every table, one sheet each" : "The table, as a sheet" });
  }
  return list;
}

const stillToFill = (blanks: number) => `${blanks} ${blanks === 1 ? "blank" : "blanks"} still to fill`;

// A draft made from a meeting (docs/superpowers/specs/2026-11-24-meeting-recipes-design.md, 5.2): it is
// written in the background (the page asks every 2 seconds), then read here, edited block by block,
// copied, opened in the mail app, downloaded, shared read-only with the team, regenerated (a new draft) or
// discarded. A reader of a shared draft (`can_edit` false) sees it read-only: Copy, the mail app and Download
// stay (reading is allowed), Edit, Share, Regenerate and Discard are not offered, and "Shared by <name>" says
// whose it is. Lead-designed: the designer's files draw none of this; it reuses their components, tokens and
// the Download menu's classes.
export function DocumentView({ initial }: { initial: DocumentDraft }) {
  const router = useRouter();
  const { doc, gone, replace } = useDocument(initial);
  const [editing, setEditing] = useState(false);
  const [working, setWorking] = useState<DocumentBlock[]>([]);
  const [title, setTitle] = useState(doc.title);
  const [busy, setBusy] = useState<"save" | "regenerate" | "discard" | DocumentExportFormat | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [sharing, setSharing] = useState(false);
  const menu = useRef<HTMLDetailsElement>(null);

  const meetingId = doc.meeting_ids.length === 1 ? doc.meeting_ids[0] : null;
  const { blanks, figures } = checkCounts(doc.checks);
  const recipe = doc.recipe_key ?? doc.recipe_id;
  const canEdit = doc.can_edit;
  const canRegenerate = canEdit && recipe !== null && doc.meeting_ids.length > 0;
  const sharedLine = canEdit ? sharedWithPhrase(doc.shared_with) : null;
  const isShared = sharedLine !== null;
  const mail = doc.output_type === "email" ? mailtoHref(doc.title, doc.blocks) : undefined;

  function startEditing() {
    setProblem(null);
    setNotice(null);
    setWorking(doc.blocks);
    setTitle(doc.title);
    setEditing(true);
  }

  // A board that changed in another tab: fetch it again (a failed fetch leaves what is shown).
  async function reload() {
    try {
      replace(await api.documents.get(doc.id));
    } catch {
      // the next change will say what is wrong
    }
  }

  async function save() {
    setBusy("save");
    setProblem(null);
    try {
      const next = await api.documents.edit(doc.id, { blocks: withoutHiddenSources(working), ...(title.trim() && title !== doc.title ? { title } : {}) });
      replace(next);
      setEditing(false);
    } catch (caught) {
      // `blocks_invalid` is a 422 whose message is written for the person ("Check the tables ...").
      setProblem(caught instanceof ApiError && caught.code === "blocks_invalid" ? caught.message : errorMessage(caught));
    }
    setBusy(null);
  }

  async function download(entry: Download) {
    if (busy) return;
    if (menu.current) menu.current.open = false;
    setBusy(entry.format);
    setProblem(null);
    setNotice(null);
    try {
      const file = await api.documents.export(doc.id, entry.format, entry.table);
      const filename = file.filename ?? `${doc.title}.${entry.format}`;
      saveBlob(file.blob, filename);
      setNotice(blanks > 0 ? `${filename} downloaded with ${stillToFill(blanks)}.` : `${filename} downloaded`);
    } catch (caught) {
      setProblem(errorMessage(caught));
    }
    setBusy(null);
  }

  async function regenerate() {
    if (busy || recipe === null) return;
    setBusy("regenerate");
    setProblem(null);
    try {
      const made = await api.documents.create({
        ...(doc.recipe_key ? { recipe_key: doc.recipe_key } : { recipe_id: doc.recipe_id }),
        meeting_ids: doc.meeting_ids,
        options: doc.options,
        use_brief: doc.use_brief,
      });
      router.push(`/documents/${made.id}`);
    } catch (caught) {
      setProblem(errorMessage(caught));
      setBusy(null);
    }
  }

  async function discard() {
    setBusy("discard");
    setProblem(null);
    try {
      await api.documents.remove(doc.id);
      router.push(meetingId !== null ? `/meetings/${meetingId}` : "/");
    } catch (caught) {
      // Already gone is as good as discarded.
      if (caught instanceof ApiError && caught.status === 404) router.push(meetingId !== null ? `/meetings/${meetingId}` : "/");
      else {
        setProblem(errorMessage(caught));
        setBusy(null);
      }
    }
  }

  const back = !canEdit
    ? { href: "/documents?tab=shared", label: "Back to documents" }
    : meetingId !== null ? { href: `/meetings/${meetingId}`, label: "Back to the meeting" } : { href: "/", label: "All meetings" };
  const ready = doc.status === "ready";

  return (
    <div className="stack-lg">
      <AppLink className="crumb" href={back.href}>
        <Icon name="back" size={14} />
        {back.label}
      </AppLink>
      <div className="detail-bar">
        <div className="detail-head">
          <div className="detail-title">
            <h1>{doc.title}</h1>
            <Badge tone={doc.status === "failed" ? "danger" : doc.status === "ready" ? "success" : "info"} dot>
              {doc.status === "writing" ? "Writing" : doc.status === "failed" ? "Failed" : "Ready"}
            </Badge>
          </div>
          <p className="meta-line">
            <span>{KIND_NAME[doc.output_type]}</span>
            {!canEdit && doc.owner_name ? <span>Shared by {doc.owner_name}</span> : null}
            {sharedLine ? <span>{sharedLine}</span> : null}
            {doc.edited ? <span>Edited</span> : null}
          </p>
        </div>
      </div>

      {gone ? (
        <Alert kind="notice">This draft was discarded. It is no longer here.</Alert>
      ) : doc.status === "writing" ? (
        <Alert kind="info" role="status">
          Writing your draft... This page updates by itself; you can leave and come back.
        </Alert>
      ) : doc.status === "failed" ? (
        <Alert kind="error">{doc.failure_message ?? "The draft could not be written. Try again."}</Alert>
      ) : null}

      {ready && doc.stale ? (
        <Alert kind="notice">This draft is older than the meeting: the notes or transcript changed after it was made. Regenerate to make a new one from the latest.</Alert>
      ) : null}
      {ready && !doc.sources_available ? (
        <Alert kind="notice">
          {canEdit
            ? "The meetings this draft came from are no longer available to you, so its sources cannot be opened."
            : "You cannot open the meetings this draft was made from, so its sources are not available to you."}
        </Alert>
      ) : null}
      {ready && doc.left_out_count > 0 ? (
        <Alert kind="notice">
          {doc.left_out_count} {doc.left_out_count === 1 ? "meeting was" : "meetings were"} left out because you cannot read {doc.left_out_count === 1 ? "it" : "them"}.
        </Alert>
      ) : null}

      {ready && !editing ? (
        <div className="doc-bar">
          <div className="doc-actions">
            <CopyButton
              text={blocksToText(doc.blocks)}
              label="Copy"
              onCopied={() => setNotice(blanks > 0 ? `Copied with ${stillToFill(blanks)}.` : null)}
            />
            {mail === undefined ? null : mail !== null ? (
              <Button kind="secondary" size="sm" href={mail}>
                <Icon name="mail" size={14} />
                Open in mail app
              </Button>
            ) : (
              <>
                <CopyButton text={blocksToText(doc.blocks)} label="Open in mail app" onCopied={() => setNotice(blanks > 0 ? `Copied with ${stillToFill(blanks)}.` : null)} />
                <span className="meta-line">Too long for a mail link, so this copies the email: paste it into your mail app.</span>
              </>
            )}
            <details className="menu dl-menu" ref={menu}>
              <summary className="btn btn-secondary btn-sm" aria-haspopup="true">
                <Icon name="download" size={14} />
                {busy && busy !== "save" && busy !== "regenerate" && busy !== "discard" ? "Preparing..." : "Download"}
                <Icon name="down" size={14} />
              </summary>
              <div className="menu-panel dl-panel" role="group" aria-label="Download">
                {downloads(doc.blocks).map((entry) => (
                  <button key={entry.key} type="button" className="dl-option" disabled={busy !== null} onClick={() => void download(entry)}>
                    <span className={`dl-tile dl-tile-${entry.format === "docx" || entry.format === "pdf" ? entry.format : "file"}`} aria-hidden="true">
                      {entry.tag}
                    </span>
                    <span className="dl-text">
                      <span className="dl-label">{entry.label}</span>
                      <span className="dl-sub">{entry.sub}</span>
                    </span>
                  </button>
                ))}
              </div>
            </details>
            {canEdit && doc.output_type !== "board" ? (
              <Button kind="secondary" size="sm" type="button" onClick={startEditing}>
                Edit
              </Button>
            ) : null}
            {canEdit ? (
              <Button kind="secondary" size="sm" type="button" onClick={() => setSharing(true)}>
                <Icon name="send" size={14} />
                {isShared ? "Shared" : "Share with team"}
              </Button>
            ) : null}
            {canRegenerate ? (
              <Button kind="secondary" size="sm" type="button" disabled={busy !== null} onClick={() => void regenerate()}>
                <Icon name="rewrite" size={14} />
                Regenerate
              </Button>
            ) : null}
            {canEdit && !confirming ? (
              <Button kind="quiet" size="sm" type="button" onClick={() => setConfirming(true)}>
                <Icon name="trash" size={14} />
                Discard
              </Button>
            ) : null}
          </div>
          {blanks > 0 || figures > 0 ? (
            <div className="doc-checks">
              {blanks > 0 ? <Badge tone="warning">{blanksPhrase(blanks)}</Badge> : null}
              {figures > 0 ? <Badge tone="warning">{figuresPhrase(figures)}</Badge> : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {doc.status !== "ready" && !gone && canEdit ? (
        <div className="doc-actions">
          {doc.status === "failed" && canRegenerate ? (
            <Button size="sm" type="button" disabled={busy !== null} onClick={() => void regenerate()}>
              <Icon name="rewrite" size={14} />
              Regenerate
            </Button>
          ) : null}
          {!confirming ? (
            <Button kind="quiet" size="sm" type="button" onClick={() => setConfirming(true)}>
              <Icon name="trash" size={14} />
              Discard
            </Button>
          ) : null}
        </div>
      ) : null}

      {confirming ? (
        <Alert kind="notice">
          <p>Discard this draft? This cannot be undone.</p>
          <div className="doc-actions">
            <Button kind="danger" size="sm" type="button" disabled={busy !== null} onClick={() => void discard()}>
              Discard draft
            </Button>
            <Button kind="secondary" size="sm" type="button" onClick={() => setConfirming(false)}>
              Keep
            </Button>
          </div>
        </Alert>
      ) : null}

      {notice ? (
        <Alert kind={notice.includes("still to fill") ? "notice" : "info"} role="status">
          {notice}
        </Alert>
      ) : null}
      {problem ? <Alert kind="error">{problem}</Alert> : null}

      {ready ? (
        <div className="card doc-card">
          {editing ? (
            <>
              <Field id="doc-title" label="Title" value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} />
              <DocumentBody doc={doc} blocks={working} editing onBlocks={setWorking} />
              <div className="doc-actions">
                <Button size="sm" type="button" disabled={busy === "save"} onClick={() => void save()}>
                  {busy === "save" ? "Saving..." : "Save changes"}
                </Button>
                <Button kind="secondary" size="sm" type="button" disabled={busy === "save"} onClick={() => setEditing(false)}>
                  Cancel
                </Button>
              </div>
            </>
          ) : (
            <DocumentBody
              doc={doc}
              blocks={doc.blocks}
              editing={false}
              onBlocks={() => {}}
              canEdit={canEdit}
              onSaved={replace}
              onStale={reload}
            />
          )}
        </div>
      ) : null}
      {canEdit && ready ? <DocumentShareDialog open={sharing} doc={doc} onClose={() => setSharing(false)} onChanged={replace} /> : null}
    </div>
  );
}
