"use client";

import { useState } from "react";
import { AppLink, Badge, Alert, Button, Card, EmptyState, PageHead } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { Notice } from "@/lib/api/types";
import { LocalTime } from "@/components/app/local-time";

// Where a notice points. The API has no link field: the only target is the meeting a notice is about.
export function noticeLink(notice: Notice): { href: string; label: string } | null {
  if (notice.meeting_id !== null) return { href: `/meetings/${notice.meeting_id}`, label: "Open the meeting" };
  return null;
}

// The bell, as a page: what the app has told the user, newest first, with a way to mark it all read.
// There is no designed screen for it (docs/ui has none), so it is a plain list in the usual frame.
export function NotificationsView({ initial }: { initial: Notice[] }) {
  const [notices, setNotices] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const unread = notices.filter((n) => n.read_at === null).length;

  async function readAll() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.notifications.readAll();
      const now = new Date().toISOString();
      setNotices((current) => current.map((n) => (n.read_at === null ? { ...n, read_at: now } : n)));
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setBusy(false);
  }

  return (
    <div className="stack-md measure">
      <PageHead title="Notifications" subtitle="What the note taker has told you.">
        {unread > 0 ? (
          <Button kind="secondary" disabled={busy} onClick={() => void readAll()}>
            Mark all as read
          </Button>
        ) : null}
      </PageHead>
      {error ? <Alert kind="error">{error}</Alert> : null}
      {notices.length > 0 ? (
        <Card flush>
          <ul className="list">
            {notices.map((notice) => {
              const link = noticeLink(notice);
              return (
                <li className="notice-row" key={notice.id}>
                  <div>
                    <strong>{notice.title}</strong>{" "}
                    {notice.read_at === null ? <Badge tone={notice.severity === "warning" ? "warning" : "brand"}>New</Badge> : null}
                  </div>
                  <p>{notice.body}</p>
                  <div className="muted">
                    <LocalTime iso={notice.created_at} kind="day" />, <LocalTime iso={notice.created_at} />
                    {link ? (
                      <>
                        {" · "}
                        <AppLink href={link.href}>{link.label}</AppLink>
                      </>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>
      ) : (
        <Card>
          <EmptyState title="Nothing yet.">Notices about your account and your meetings will show up here.</EmptyState>
        </Card>
      )}
    </div>
  );
}
