"use client";

import { useEffect, useState } from "react";
import { AppLink, Card } from "@/components/ui";
import { api } from "@/lib/api/browser";
import type { DocumentRow } from "@/lib/api/types";

// "Documents from this meeting": the drafts made from this meeting that the person may open (their own, and
// a teammate's shared one when they can read the meeting), each a link to the draft. LEAD-DESIGNED, small:
// a card in the meeting's side column, drawn only when there is at least one. A failed or missing answer
// draws nothing: the Make button and the Documents page are the way to the drafts.
export function MeetingDocuments({ meetingId }: { meetingId: number }) {
  const [rows, setRows] = useState<DocumentRow[]>([]);
  useEffect(() => {
    let live = true;
    Promise.resolve()
      .then(() => api.documents.list({ meetingId, limit: 10 }))
      .then((found) => live && setRows(found.documents))
      .catch(() => live && setRows([]));
    return () => {
      live = false;
    };
  }, [meetingId]);
  if (rows.length === 0) return null;
  return (
    <Card compact>
      <h2 className="side-title">Documents from this meeting</h2>
      <ul className="side-docs">
        {rows.map((row) => (
          <li className="side-doc" key={row.id}>
            <AppLink className="det-link" href={`/documents/${row.id}`}>
              {row.title}
            </AppLink>
            {row.access === "shared" && row.owner_name ? <span className="meta">Shared by {row.owner_name}</span> : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}
