"use client";

import { useState } from "react";
import { LocalTime } from "@/components/app/local-time";
import { Alert, Badge, Button, Card, EmptyState, Icon, ListRow, PageHead, TabButtons } from "@/components/ui";
import { api } from "@/lib/api/browser";
import type { DocumentList, DocumentRow } from "@/lib/api/types";
import { DOCUMENT_PAGE, OUTPUT_NAME, STATUS_NAME, sharedWithPhrase } from "@/lib/documents";

export type DocumentsTab = "mine" | "shared";

type Page = { rows: DocumentRow[]; more: boolean };

const TABS = [
  { id: "mine", label: "Mine" },
  { id: "shared", label: "Shared with me" },
] as const;

const EMPTY: Record<DocumentsTab, { title: string; text: string }> = {
  mine: {
    title: "No drafts yet.",
    text: "Open a meeting and press Make to write a follow-up, a proposal or another document from it. Your drafts are listed here.",
  },
  shared: {
    title: "Nothing has been shared with you.",
    text: "When a teammate shares a draft with your team, you can read it here. You cannot edit it.",
  },
};

const FAILED: Record<DocumentsTab, string> = {
  mine: "We could not load your drafts.",
  shared: "We could not load the drafts shared with you.",
};

const toPage = (list: DocumentList): Page => ({ rows: list.documents, more: list.documents.length >= DOCUMENT_PAGE });

// The Documents page: the drafts the person made ("Mine") and the ones teammates shared with their team
// ("Shared with me", read-only), newest first. LEAD-DESIGNED, not in the designer's files; built from the
// Meetings list's pieces (card, rows, tabs, "Show older" button). The first page of the opening tab comes from
// the server render; the other tab is asked for when it is first opened; "Load more" asks for the next page
// with `before_id` (the smallest id shown).
export function DocumentsView({ initialTab, initial }: { initialTab: DocumentsTab; initial: DocumentList }) {
  const [tab, setTab] = useState<DocumentsTab>(initialTab);
  const [pages, setPages] = useState<Partial<Record<DocumentsTab, Page>>>({ [initialTab]: toPage(initial) });
  const [loading, setLoading] = useState<DocumentsTab | null>(null);
  const [failed, setFailed] = useState<DocumentsTab | null>(null);

  async function load(which: DocumentsTab, beforeId?: number) {
    setLoading(which);
    setFailed(null);
    try {
      const list = await api.documents.list({
        ...(which === "mine" ? { mine: true } : { shared: true }),
        limit: DOCUMENT_PAGE,
        ...(beforeId !== undefined ? { beforeId } : {}),
      });
      const next = toPage(list);
      setPages((all) => ({ ...all, [which]: beforeId !== undefined ? { rows: [...(all[which]?.rows ?? []), ...next.rows], more: next.more } : next }));
    } catch {
      setFailed(which);
    }
    setLoading(null);
  }

  function choose(id: string) {
    const which: DocumentsTab = id === "shared" ? "shared" : "mine";
    setTab(which);
    if (!pages[which] && loading !== which) void load(which);
  }

  const page = pages[tab];
  const rows = page?.rows ?? [];
  const smallest = rows.length > 0 ? Math.min(...rows.map((r) => r.id)) : undefined;
  const busy = loading === tab;

  return (
    <>
      <PageHead title="Documents" subtitle="Drafts written from your meetings, and the ones your teammates share." />
      <Card flush>
        <div className="panel-bar">
          <TabButtons items={TABS} defaultId={initialTab} onChange={choose} label="Documents" />
        </div>
        <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
          {failed === tab && !page ? (
            <div className="empty">
              <Alert kind="error">{FAILED[tab]}</Alert>
              <Button kind="secondary" size="sm" type="button" onClick={() => void load(tab)}>
                Try again
              </Button>
            </div>
          ) : !page ? (
            <div className="empty" role="status">
              Loading drafts...
            </div>
          ) : rows.length === 0 ? (
            <EmptyState title={EMPTY[tab].title}>{EMPTY[tab].text}</EmptyState>
          ) : (
            <>
              <ul className="list" aria-label={tab === "mine" ? "Your drafts" : "Drafts shared with you"}>
                {rows.map((row) => (
                  <ListRow
                    key={row.id}
                    href={`/documents/${row.id}`}
                    end={
                      <span className="row-end">
                        <Badge tone={row.status === "failed" ? "danger" : row.status === "ready" ? "success" : "info"} dot>
                          {STATUS_NAME[row.status]}
                        </Badge>
                        <span className="list-row-chevron">
                          <Icon name="chevron" />
                        </span>
                      </span>
                    }
                  >
                    <span className="list-row-main">
                      <span className="tile">
                        <Icon name="copy" size={18} />
                      </span>
                      <span className="list-row-text">
                        <span className="list-title">{row.title}</span>
                        <span className="meta-line">
                          <span>{OUTPUT_NAME[row.output_type]}</span>
                          <span>
                            <LocalTime iso={row.created_at} />
                          </span>
                          {row.access === "shared" && row.owner_name ? <span>Shared by {row.owner_name}</span> : null}
                          {row.access !== "shared" && sharedWithPhrase(row.shared_with) ? <span>{sharedWithPhrase(row.shared_with)}</span> : null}
                        </span>
                      </span>
                    </span>
                  </ListRow>
                ))}
              </ul>
              {page.more || failed === tab ? (
                <div className="card-foot">
                  {page.more ? (
                    <Button kind="secondary" size="sm" type="button" disabled={busy} onClick={() => void load(tab, smallest)}>
                      {busy ? "Loading..." : "Load more"}
                    </Button>
                  ) : null}
                  {failed === tab ? <span className="meta">Could not load more drafts. Try again.</span> : null}
                </div>
              ) : null}
            </>
          )}
        </div>
      </Card>
    </>
  );
}
