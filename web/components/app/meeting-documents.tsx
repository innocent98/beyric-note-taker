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

// Said inside "Delete this meeting?" (spec 5.4a): the person's own documents made from this meeting alone
// are deleted with it, so the confirmation says how many, and that one made from several meetings stays.
// Draws nothing while it asks, when it fails, or when there are none.
export function DraftsGoWithIt({ meetingId }: { meetingId: number }) {
  const [alone, setAlone] = useState(0);
  const [several, setSeveral] = useState(0);
  useEffect(() => {
    let live = true;
    Promise.resolve()
      .then(() => api.documents.list({ meetingId, limit: 100 }))
      .then((found) => {
        if (!live) return;
        const own = found.documents.filter((row) => row.access === "owner");
        setAlone(own.filter((row) => row.meeting_ids.length === 1).length);
        setSeveral(own.filter((row) => row.meeting_ids.length > 1).length);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [meetingId]);
  if (alone === 0 && several === 0) return null;
  return (
    <p>
      {alone > 0 ? `This also deletes the ${alone === 1 ? "document" : `${alone} documents`} made only from it.` : ""}
      {alone > 0 && several > 0 ? " " : ""}
      {several > 0 ? `${several === 1 ? "A document" : `${several} documents`} made from several meetings stays; its link to this meeting will read not available.` : ""}
    </p>
  );
}
