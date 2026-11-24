"use client";

import { useEffect, useRef, useState } from "react";
import { Alert, Button, Icon } from "@/components/ui";
import { api } from "@/lib/api/browser";
import type { DocumentDraft, RecipeTeam } from "@/lib/api/types";
import { shareErrorMessage, sharedWithPhrase } from "@/lib/documents";

// "Share with team": the lead-designed dialog behind the Share button of the draft's toolbar (the designer's
// files draw nothing of recipes). It uses the Make dialog's frame. A draft is private to its creator until
// shared, read-only, with the active team (the team the Make dialog names: the tab's team, as the brief
// answers it); teammates read the text even where they cannot open the meetings it came from, which the dialog
// says first. A shared draft shows who it is shared with and Stop sharing, which ends the access for every team
// at once. `onChanged` takes the draft as it now stands; the dialog closes after a change.
export function DocumentShareDialog({
  open,
  doc,
  onClose,
  onChanged,
}: {
  open: boolean;
  doc: DocumentDraft;
  onClose: () => void;
  onChanged: (next: DocumentDraft) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [team, setTeam] = useState<RecipeTeam | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState<"share" | "unshare" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const asking = useRef(false);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setTeam(null);
      setLoadError(false);
      setBusy(null);
      setError(null);
      asking.current = false;
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    if (!open && dialog.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
  }, [open]);

  // The team the draft would be shared with: the one the Make dialog names (the tab's team, else the person's
  // default), from the brief's answer, which carries it.
  useEffect(() => {
    if (!open) return;
    let live = true;
    api.brief
      .get()
      .then((found) => live && setTeam(found.team))
      .catch(() => live && setLoadError(true));
    return () => {
      live = false;
    };
  }, [open, reload]);

  const sharedLine = sharedWithPhrase(doc.shared_with);
  const sharedAlready = (doc.shared_with ?? []).some((t) => t.id === team?.id);

  async function share() {
    if (!team || asking.current) return;
    asking.current = true;
    setBusy("share");
    setError(null);
    try {
      onChanged(await api.documents.share(doc.id, team.id));
      onClose();
    } catch (caught) {
      setError(shareErrorMessage(caught, "share"));
      asking.current = false;
      setBusy(null);
    }
  }

  async function stop() {
    if (asking.current) return;
    asking.current = true;
    setBusy("unshare");
    setError(null);
    try {
      await api.documents.unshare(doc.id);
      onChanged({ ...doc, shared_with: [] });
      onClose();
    } catch (caught) {
      setError(shareErrorMessage(caught, "unshare"));
      asking.current = false;
      setBusy(null);
    }
  }

  return (
    <dialog
      ref={ref}
      className="dialog tm-dialog sh-dialog"
      aria-labelledby="ds-title"
      onClose={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      {open ? (
        <>
          <div className="sh-head">
            <div>
              <h2 className="dialog-title" id="ds-title">
                Share this draft with your team
              </h2>
              <p className="tm-dialog-sub">{doc.title}</p>
            </div>
            <button type="button" className="sh-close" aria-label="Close" onClick={onClose}>
              <Icon name="close" size={16} />
            </button>
          </div>
          <div className="tm-dialog-body">
            {sharedLine ? <p className="sh-shared">{sharedLine}</p> : null}
            <p>Teammates will read this draft even if they cannot open the meeting.</p>
            <p className="field-hint">They cannot edit it. You can stop sharing at any time, and they lose access at once.</p>
            {loadError ? (
              <Alert kind="error">
                We could not find out which team this would be shared with.{" "}
                <Button
                  kind="quiet"
                  type="button"
                  onClick={() => {
                    setLoadError(false);
                    setReload((n) => n + 1);
                  }}
                >
                  Try again
                </Button>
              </Alert>
            ) : null}
            {error ? <Alert kind="error">{error}</Alert> : null}
          </div>
          <div className="sh-foot">
            <span className="sh-count" />
            <div className="sh-foot-actions">
              <Button kind="secondary" size="md" type="button" onClick={onClose}>
                Cancel
              </Button>
              {(doc.shared_with ?? []).length > 0 ? (
                <Button kind="danger" size="md" type="button" disabled={busy !== null} onClick={() => void stop()}>
                  {busy === "unshare" ? "Stopping..." : "Stop sharing"}
                </Button>
              ) : null}
              {sharedAlready ? null : (
                <Button size="md" type="button" disabled={busy !== null || !team} onClick={() => void share()}>
                  {busy === "share" ? "Sharing..." : team ? `Share with ${team.name}` : "Share with team"}
                </Button>
              )}
            </div>
          </div>
        </>
      ) : null}
    </dialog>
  );
}
