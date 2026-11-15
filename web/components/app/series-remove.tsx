"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Alert, Button } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { SeriesPage } from "@/lib/api/types";
import { SERIES_REMOVED_FLAG } from "@/lib/deleted-flag";
import { plural } from "@/lib/format";
import { teamLabel } from "@/lib/series-team";

// "Remove this recurring meeting" at the foot of the Series page. LEAD-DESIGNED (Adebayo, 2026-11-15): the
// designer drew no such control. It reuses the Meeting page's "Delete meeting" pieces exactly (the red text
// button, the inline ask in the same box, Cancel and the danger button): docs/ui has nothing to add.
//
// Drawn only when the server says the caller may (`can_remove`: an owner or admin of the row's team, the owner of a
// personal row, or the person who made a row that is completely empty). It acts on THIS page's row only; the ask says what happens to
// everything that was in it. Nothing is deleted but the row, the waiting agenda lines and the repeat topics:
// the meetings only stop being grouped. A 404 means it is already gone, which is what the person wanted.
export function SeriesRemove({ page, multiTeam }: { page: SeriesPage; multiTeam: boolean }) {
  const router = useRouter();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.series.remove(page.id);
    } catch (caught) {
      if (!(caught instanceof ApiError && caught.status === 404)) {
        setError(errorMessage(caught));
        setAsking(false);
        setBusy(false);
        return;
      }
    }
    try {
      sessionStorage.setItem(SERIES_REMOVED_FLAG, "1");
    } catch {
      // Private mode: the list is shown without the toast.
    }
    router.push("/");
  }

  const lines = page.agenda.length;
  const personal = page.team.is_personal;
  // Nothing the person can see is grouped or waiting under it: the question is the short one.
  const empty = page.past_meetings.length === 0 && lines === 0;
  return (
    <div>
      <div role="status" aria-live="polite">
        {error ? <Alert kind="error">{error}</Alert> : null}
      </div>
      {asking ? (
        <div className="meeting-delete-ask" role="group" aria-label="Remove this recurring meeting?">
          <p>
            <strong>Remove this recurring meeting?</strong>{" "}
            {empty
              ? "No meeting is grouped under it and nothing is waiting in it."
              : personal
                ? "Your past meetings stay. They will not be grouped any more. No meeting is deleted."
                : `This removes it for everyone in ${page.team.name}. The past meetings stay. They will not be grouped any more. No meeting is deleted.`}{" "}
            You can&apos;t undo this.
          </p>
          <ul className="sr-remove-list">
            {empty ? null : (
              <>
                <li>
                  <strong>Past meetings:</strong> they stay as ordinary meetings, with their notes, tasks, decisions, open
                  questions and sharing.
                </li>
                <li>
                  <strong>Waiting agenda items:</strong>{" "}
                  {lines > 0 ? `${plural(lines, "line")} will be removed.` : "none are waiting."}
                </li>
                <li>
                  <strong>Linked project:</strong> if one is linked, the project stays and only the link goes.
                </li>
                <li>
                  <strong>Keeps coming up:</strong> these topics are removed with it.
                </li>
                <li>
                  <strong>Tasks and open questions:</strong> they stay with their meetings.
                </li>
              </>
            )}
            <li>
              <strong>Your calendar:</strong> if this came from a calendar event, it will not be made again, and later
              meetings of that event will not be grouped here.
            </li>
            {multiTeam ? (
              <li>
                <strong>Other pages:</strong>{" "}
                {personal
                  ? "this removes it from your Personal space only. The same recurring meeting on a team's page is not changed."
                  : `this removes it from ${teamLabel(page.team)} only. The same recurring meeting on another team's page, or on your personal page, is not changed.`}
              </li>
            ) : null}
          </ul>
          <div className="dec-ask-actions">
            <Button kind="secondary" size="sm" type="button" disabled={busy} onClick={() => setAsking(false)}>
              Cancel
            </Button>
            <Button kind="danger" size="sm" type="button" disabled={busy} onClick={() => void remove()}>
              {busy ? "Removing..." : "Remove recurring meeting"}
            </Button>
          </div>
        </div>
      ) : (
        <div className="meeting-delete-row">
          <button type="button" className="meeting-delete" disabled={busy} onClick={() => setAsking(true)}>
            Remove this recurring meeting
          </button>
        </div>
      )}
    </div>
  );
}
