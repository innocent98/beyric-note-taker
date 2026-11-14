"use client";

import { useEffect, useState } from "react";
import { Alert, Button, Field, SelectField } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { SeriesCard, SeriesRef } from "@/lib/api/types";

// LEAD-DESIGNED, not in the designer's file (approved by Adebayo, 2026-11-06): the body of the
// dialog behind "Add to recurring meeting" in the meeting page's Details (spec 7, ambiguity row 8).
// Built from the file's own dialog, select and button patterns: pick one of the team's recurring
// meetings, or "New recurring meeting" and name it, or (for a meeting already in one) take it out.
// Only the meeting's owner sees the entry point. The dialog itself is a native <dialog>
// (`components/ui/dialog.tsx`); closing it is done here after a save.
const NEW = "new";
const NONE = "none";

export function SeriesPicker({
  dialogId,
  meetingId,
  current,
  team,
  onDone,
}: {
  dialogId: string;
  meetingId: number;
  current: SeriesRef | null;
  // The team the MEETING belongs to (multi-team: a meeting joins only a series of its own team); undefined = as before.
  team?: number | null;
  onDone: () => void;
}) {
  const [cards, setCards] = useState<SeriesCard[]>([]);
  const [choice, setChoice] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The team's recurring meetings are read when the dialog is opened (it sits in the page closed),
  // so a meeting page costs nothing for someone who never opens it.
  useEffect(() => {
    const dialog = document.getElementById(dialogId) as HTMLDialogElement | null;
    if (!dialog) return;
    let live = true;
    const load = () => {
      if (!dialog.open) return;
      api.series
        .list(team)
        .then((answer) => {
          if (live) setCards(answer.series);
        })
        .catch(() => {
          // the list stays as it was: "New recurring meeting" still works
        });
    };
    const watch = new MutationObserver(load);
    watch.observe(dialog, { attributes: true, attributeFilter: ["open"] });
    return () => {
      live = false;
      watch.disconnect();
    };
  }, [dialogId, team]);

  const options: Array<readonly [string, string]> = [
    ["", "Choose a recurring meeting"],
    ...cards.map((card) => [String(card.id), card.title] as const),
    [NEW, "New recurring meeting"],
    ...(current ? ([[NONE, "Not in a recurring meeting"]] as const) : []),
  ];

  const close = () => (document.getElementById(dialogId) as HTMLDialogElement | null)?.close();
  const ready = choice === NONE || (choice === NEW ? title.trim().length > 0 : choice !== "");

  async function save() {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (choice === NONE) await api.meetings.setSeries(meetingId, null);
      else if (choice === NEW) await api.meetings.setSeries(meetingId, (await api.series.create(title.trim(), team)).id);
      else await api.meetings.setSeries(meetingId, Number(choice));
      close();
      onDone();
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setBusy(false);
  }

  return (
    <form
      className="sp-form"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <p className="sp-lead">
        A recurring meeting keeps its unfinished tasks, open questions and agenda from one meeting to the next.
      </p>
      <SelectField
        id={`${dialogId}-choice`}
        label="Recurring meeting"
        value={choice}
        onChange={(event) => setChoice(event.target.value)}
        options={options}
      />
      {choice === NEW ? (
        <Field
          id={`${dialogId}-name`}
          label="Name"
          value={title}
          maxLength={200}
          placeholder="Monday planning"
          onChange={(event) => setTitle(event.target.value)}
        />
      ) : null}
      <div role="status" aria-live="polite">
        {error ? <Alert kind="error">{error}</Alert> : null}
      </div>
      <div className="dialog-actions">
        <Button kind="secondary" type="button" data-dialog-close>
          Cancel
        </Button>
        <Button type="submit" disabled={!ready || busy}>
          {choice === NONE ? "Take out" : "Add"}
        </Button>
      </div>
    </form>
  );
}
