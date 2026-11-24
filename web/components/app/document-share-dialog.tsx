"use client";

import { useEffect, useRef, useState } from "react";
import { Alert, Button, Icon } from "@/components/ui";
import { api } from "@/lib/api/browser";
import type { DocumentDraft } from "@/lib/api/types";
import { shareErrorMessage, sharedWithPhrase } from "@/lib/documents";

// "Share with team": the lead-designed dialog behind the Share button of the draft's toolbar (the designer's
// files draw nothing of recipes). It uses the Make dialog's frame. A draft is private to its creator until
// shared, read-only, with THE TEAM IT WAS MADE IN (`doc.team`; never the tab's team). Teammates read the draft's
// title and text even where they cannot open the meetings it came from, which the dialog says first, and a
// reader who cannot read every source meeting is shown a generic title instead of the real one. When the
// server says the draft cannot be shared (`share_blocked`: made in the person's own space, or its team left)
// the dialog shows that sentence and offers no Share. A shared draft shows who it is shared with and Stop
// sharing, which ends the access for every team at once. `onChanged` takes the draft as it now stands.
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
  const [busy, setBusy] = useState<"share" | "unshare" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const asking = useRef(false);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
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

  const team = doc.team ?? null;
  const blocked = doc.share_blocked ?? null;
  const sharedLine = sharedWithPhrase(doc.shared_with);
  const sharedAlready = (doc.shared_with ?? []).some((t) => t.id === team?.id);

  async function share() {
    if (!team || blocked || asking.current) return;
    asking.current = true;
    setBusy("share");
    setError(null);
    try {
      onChanged(await api.documents.share(doc.id));
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
            <p className="field-hint">The draft&apos;s title and text can reveal what was said in it. A teammate who cannot open all its meetings sees a generic title instead of this one.</p>
            <p className="field-hint">They cannot edit it. You can stop sharing at any time, and they lose access at once.</p>
            {blocked ? <Alert kind="notice">{blocked}</Alert> : null}
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
                <Button size="md" type="button" disabled={busy !== null || !team || blocked !== null} onClick={() => void share()}>
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
