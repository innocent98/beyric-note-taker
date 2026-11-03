"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Badge, Button, Card, EmptyState, Icon, ListRow } from "@/components/ui";
import { api } from "@/lib/api/browser";
import type { MeetingList as MeetingListData, MeetingSummary, Template } from "@/lib/api/types";
import { formatTime, plural } from "@/lib/format";

// The list of meetings, newest first. The first page comes from the server render; older pages are
// fetched on request with before_id (docs/fe-integration-guide-owner-app.md 5.1). The page also
// refreshes when the tab comes back into view, as the guide suggests (the list does not poll).
export function MeetingList({ initial, templates }: { initial: MeetingListData; templates: Template[] }) {
  const router = useRouter();
  const [older, setOlder] = useState<MeetingSummary[]>([]);
  const [paging, setPaging] = useState<{ hasMore: boolean; before: number | null } | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") router.refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [router]);

  const shown = new Set(initial.meetings.map((m) => m.id));
  const meetings = [...initial.meetings, ...older.filter((m) => !shown.has(m.id))];
  const hasMore = paging ? paging.hasMore : initial.has_more;
  const before = paging ? paging.before : initial.next_before_id;
  const names = new Map(templates.map((t) => [t.id, t.name]));

  async function loadOlder() {
    if (before === null || loading) return;
    setLoading(true);
    setFailed(false);
    try {
      const page = await api.meetings.list({ before_id: before });
      setOlder((current) => [...current, ...page.meetings]);
      setPaging({ hasMore: page.has_more, before: page.next_before_id });
    } catch {
      setFailed(true);
    }
    setLoading(false);
  }

  return (
    <Card flush>
      {meetings.length > 0 ? (
        <>
          <div className="card-head">
            <h2>Recent</h2>
            <span className="meta">
              {plural(meetings.length, "meeting")}
              {hasMore ? "+" : ""}
            </span>
          </div>
          <ul className="list">
            {meetings.map((m) => (
              <ListRow
                key={m.id}
                href={`/meetings/${m.id}`}
                end={
                  <span className="row-end">
                    <Badge tone={m.status_tone} dot>
                      {m.status_text}
                    </Badge>
                    <span className="list-row-chevron">
                      <Icon name="chevron" />
                    </span>
                  </span>
                }
              >
                <span className="list-row-main">
                  <span className="tile">
                    <Icon name="video" size={18} />
                  </span>
                  <span className="list-row-text">
                    <span className="list-title">{m.title}</span>
                    <span className="meta-line">
                      <span>{formatTime(m.created_at)}</span>
                      {names.get(m.template_id) ? <span>{names.get(m.template_id)}</span> : null}
                    </span>
                  </span>
                </span>
              </ListRow>
            ))}
          </ul>
          {hasMore ? (
            <div className="card-foot">
              <Button kind="secondary" size="sm" type="button" onClick={loadOlder} disabled={loading}>
                {loading ? "Loading..." : "Show older meetings"}
              </Button>
              {failed ? <span className="meta">Could not load older meetings. Try again.</span> : null}
            </div>
          ) : null}
        </>
      ) : (
        <EmptyState title="No meetings yet.">Paste a meeting link above to send the note taker.</EmptyState>
      )}
    </Card>
  );
}
