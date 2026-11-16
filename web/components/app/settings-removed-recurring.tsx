"use client";

import { useEffect, useState } from "react";
import { LocalTime } from "@/components/app/local-time";
import { Alert, Button } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { RemovedRecurring } from "@/lib/api/types";
import { teamLabel } from "@/lib/series-team";

// "Removed recurring meetings" in Settings, with "Bring back" (LEAD-DESIGNED, not in the designer's files; a
// follow-up of "Remove this recurring meeting", docs/fe-integration-guide-series-teams.md section 12). It reuses
// the Settings card, its head and its row pattern; nothing new is styled.
//
// Drawn only for an owner or admin of the current team, or the person in their Personal space (`team.can_manage`
// from the page; the server checks it again: GET /api/series/removed is 403 `team_forbidden` for anyone else), and
// only once the list has something in it. Nothing at all is drawn while waiting, when nothing was removed, and when
// the call fails: Settings must look as it did before.
//
// "Bring back" clears the mark, nothing else. It does not make the card at that moment, and it never regroups past
// meetings, so the line after it says when the card comes back. A 404 means it is already brought back, which is
// what the person wanted.
export function SettingsRemovedRecurring() {
  const [items, setItems] = useState<RemovedRecurring[]>([]);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ title: string; personal: boolean; team: string } | null>(null);

  useEffect(() => {
    let current = true;
    api.series
      .removed()
      .then((answer) => {
        if (current) setItems(answer.removed);
      })
      .catch(() => {
        // Not allowed, or server or network trouble: draw nothing.
      });
    return () => {
      current = false;
    };
  }, []);

  async function bringBack(item: RemovedRecurring) {
    if (busy !== null) return;
    setBusy(item.id);
    setError(null);
    try {
      await api.series.bringBack(item.id);
    } catch (caught) {
      if (!(caught instanceof ApiError && caught.status === 404)) {
        setError(errorMessage(caught));
        setBusy(null);
        return;
      }
    }
    setItems((list) => list.filter((entry) => entry.id !== item.id));
    setDone({ title: item.title, personal: item.team.is_personal, team: teamLabel(item.team) });
    setBusy(null);
  }

  if (items.length === 0 && !done) return null;
  return (
    <section className="card card-flush" aria-labelledby="set-removed">
      <div className="pl-head pl-head-plain">
        <h2 id="set-removed" className="set-title">
          Removed recurring meetings
        </h2>
        <p className="pl-sub">
          A recurring meeting you removed is not made again from your calendar. Bring one back to let it be made again.
        </p>
      </div>
      {items.map((item) => (
        <div className="pl-row pl-row-center" key={item.id}>
          <div className="pl-grow">
            <div className="pl-row-title">{item.title}</div>
            <div className="pl-row-sub">
              Removed <LocalTime iso={item.removed_at} kind="day" />
              {item.removed_by ? ` by ${item.removed_by}` : ""}
            </div>
          </div>
          <Button
            kind="secondary"
            size="sm"
            type="button"
            aria-label={`Bring back ${item.title}`}
            disabled={busy !== null}
            onClick={() => void bringBack(item)}
          >
            {busy === item.id ? "Bringing back..." : "Bring back"}
          </Button>
        </div>
      ))}
      <div role="status" aria-live="polite">
        {error ? (
          <div className="set-note">
            <Alert kind="error">{error}</Alert>
          </div>
        ) : null}
        {done ? (
          <div className="set-note">
            <Alert kind="info">
              <strong>{done.title}</strong> is brought back.{" "}
              {done.personal
                ? "Your Personal space gets its card again at the next calendar sync, within the hour, once the meeting is set to be joined."
                : `${done.team} gets its card again the next time an owner or admin shares a meeting of it, or their recording of it is shared automatically.`}{" "}
              Meetings you already recorded are not grouped into it.
            </Alert>
          </div>
        ) : null}
      </div>
    </section>
  );
}
