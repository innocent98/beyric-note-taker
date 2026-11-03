"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Alert, AppLink, Button, EmptyState, Field, Icon, PageHead } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { ReviewItem, ReviewList } from "@/lib/api/types";
import { formatDay } from "@/lib/format";

const NOT_THERE = "This item is no longer there. It may have been replaced when the notes were rewritten.";
const REWRITE_SLOW =
  "This is taking longer than expected. The rewrite may still finish: open the notes in a few minutes to see.";

// Newest meeting first, then the notes' order inside a meeting: the order the API lists them in.
function ordered(items: ReviewItem[]): ReviewItem[] {
  return [...items].sort((a, b) => b.meeting.id - a.meeting.id || a.id - b.id);
}

// After a term is added: offer to rewrite that meeting's notes with the corrected spelling. Adding a
// term does not change the notes already written (docs/fe-integration-guide-ai-workspace.md section 6).
type Offer = {
  key: string;
  meetingId: number;
  meetingTitle: string;
  term: string;
  existed: boolean;
  state: "offer" | "rewriting" | "done" | "failed";
  message: string | null;
};

// The Review screen: parts of a transcript that may have been misheard. An item can be checked (and
// unchecked), and one the model judged to be a misheard name or term (`heard_as` is not null) takes
// the corrected spelling into the vocabulary. The sidebar badge is `badges.review_open`, so after
// every change the server render is asked again (router.refresh keeps what is typed here).
export function ReviewView({ open, checked }: { open: ReviewList; checked: ReviewList }) {
  const router = useRouter();
  const [openItems, setOpenItems] = useState(open.items);
  const [checkedItems, setCheckedItems] = useState(checked.items);
  const [showChecked, setShowChecked] = useState(false);
  const [busy, setBusy] = useState<Record<number, boolean>>({});
  const busyRef = useRef(new Set<number>());
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [fixes, setFixes] = useState<Record<number, string>>({});
  const [fixErrors, setFixErrors] = useState<Record<number, string>>({});
  const [offers, setOffers] = useState<Offer[]>([]);
  const [gone, setGone] = useState(false);

  const note = (id: number, message: string | null) =>
    setErrors((current) => {
      const next = { ...current };
      if (message === null) delete next[id];
      else next[id] = message;
      return next;
    });

  // One request per item at a time; other items stay usable.
  async function run(id: number, work: () => Promise<void>) {
    if (busyRef.current.has(id)) return;
    busyRef.current.add(id);
    setBusy((current) => ({ ...current, [id]: true }));
    note(id, null);
    setGone(false);
    setFixErrors((current) => ({ ...current, [id]: "" }));
    try {
      await work();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        // Gone for good (another tab, or a rewrite): drop it from both lists and say so.
        setGone(true);
        setOpenItems((current) => current.filter((i) => i.id !== id));
        setCheckedItems((current) => current.filter((i) => i.id !== id));
        router.refresh();
      } else {
        note(id, errorMessage(caught));
      }
    }
    busyRef.current.delete(id);
    setBusy((current) => ({ ...current, [id]: false }));
  }

  function moveToChecked(item: ReviewItem) {
    setOpenItems((current) => current.filter((i) => i.id !== item.id));
    setCheckedItems((current) => ordered([...current.filter((i) => i.id !== item.id), item]));
    router.refresh();
  }

  function moveToOpen(item: ReviewItem) {
    setCheckedItems((current) => current.filter((i) => i.id !== item.id));
    setOpenItems((current) => ordered([...current.filter((i) => i.id !== item.id), item]));
    router.refresh();
  }

  const check = (item: ReviewItem) => run(item.id, async () => moveToChecked(await api.review.check(item.id)));
  const undo = (item: ReviewItem) => run(item.id, async () => moveToOpen(await api.review.uncheck(item.id)));

  function offer(item: ReviewItem, term: string, existed: boolean) {
    const key = `${item.meeting.id}:${term.toLowerCase()}`;
    const next: Offer = {
      key,
      meetingId: item.meeting.id,
      meetingTitle: item.meeting.title,
      term,
      existed,
      state: "offer",
      message: null,
    };
    setOffers((current) => [...current.filter((o) => o.key !== key), next]);
  }

  // The term is in the vocabulary now: an item that comes back to open (Undo) starts with an empty box.
  const clearFix = (id: number) => setFixes((current) => ({ ...current, [id]: "" }));

  const addTerm = (item: ReviewItem) => {
    const term = (fixes[item.id] ?? "").trim();
    if (!term) {
      setFixErrors((current) => ({ ...current, [item.id]: "Type a term to add." }));
      return Promise.resolve();
    }
    return run(item.id, async () => {
      try {
        moveToChecked((await api.review.addToVocabulary(item.id, term)).item);
        clearFix(item.id);
        offer(item, term, false);
      } catch (caught) {
        if (caught instanceof ApiError && caught.code === "duplicate_term") {
          // The API still marks the item checked when the term already exists.
          moveToChecked({ ...item, checked_at: new Date().toISOString() });
          clearFix(item.id);
          offer(item, term, true);
        } else if (caught instanceof ApiError && caught.code === "invalid_term") {
          setFixErrors((current) => ({ ...current, [item.id]: caught.message }));
        } else {
          throw caught;
        }
      }
    });
  };

  function patchOffer(key: string, patch: Partial<Offer>) {
    setOffers((current) => current.map((o) => (o.key === key ? { ...o, ...patch } : o)));
  }

  // Rewrite with the template the meeting already uses. The call waits for the model (minutes), the
  // same call the meeting page's "Rewrite notes" makes.
  async function rewrite(o: Offer) {
    patchOffer(o.key, { state: "rewriting", message: null });
    try {
      const meeting = await api.meetings.get(o.meetingId);
      if (!meeting.can_regenerate) {
        patchOffer(o.key, { state: "failed", message: "These notes cannot be rewritten right now." });
        return;
      }
      await api.meetings.regenerate(o.meetingId, meeting.template_id);
      patchOffer(o.key, { state: "done" });
      router.refresh();
    } catch (caught) {
      const slow = caught instanceof ApiError && (caught.code === "gateway_error" || caught.code === "network_error");
      patchOffer(o.key, { state: "failed", message: slow ? REWRITE_SLOW : errorMessage(caught) });
    }
  }

  const nothingAtAll = openItems.length === 0 && checkedItems.length === 0;

  return (
    <div className="stack-md measure-wide">
      <PageHead
        title="Review"
        subtitle="Parts of transcripts that may have been misheard. Check them against the transcript and fix names in your vocabulary."
      />

      {offers.map((o) => (
        <Alert kind="info" role="status" key={o.key}>
          <p className="alert-text">
            {o.state === "done" ? (
              <>The notes of {o.meetingTitle} were rewritten.</>
            ) : o.existed ? (
              <>
                <strong>{o.term}</strong> is already in your vocabulary, so nothing was added. The notes of {o.meetingTitle} may
                still have the old spelling.
              </>
            ) : (
              <>
                Added <strong>{o.term}</strong> to your vocabulary. The notes of {o.meetingTitle} still have the old spelling.
              </>
            )}
          </p>
          {o.state === "offer" || o.state === "failed" ? (
            <>
              <p className="alert-text">
                The new notes replace the old ones. Decisions you removed and items you checked may come back if the
                wording changes.
              </p>
              {o.message ? <p className="alert-text">{o.message}</p> : null}
              <div className="alert-actions">
                <Button size="sm" type="button" onClick={() => void rewrite(o)}>
                  Rewrite the notes with the corrected spelling
                </Button>
                <Button
                  kind="quiet"
                  type="button"
                  onClick={() => setOffers((current) => current.filter((x) => x.key !== o.key))}
                >
                  Not now
                </Button>
              </div>
            </>
          ) : o.state === "rewriting" ? (
            <p className="alert-text">Rewriting the notes. This can take a few minutes; keep this page open.</p>
          ) : (
            <div className="alert-actions">
              <Button kind="secondary" size="sm" href={`/meetings/${o.meetingId}`}>
                Open notes
              </Button>
              <Button
                kind="quiet"
                type="button"
                onClick={() => setOffers((current) => current.filter((x) => x.key !== o.key))}
              >
                Dismiss
              </Button>
            </div>
          )}
        </Alert>
      ))}

      {gone ? <Alert kind="notice">{NOT_THERE}</Alert> : null}

      {open.truncated ? (
        <Alert kind="notice">Showing the 200 newest items. Check some off and reload the page to see the rest.</Alert>
      ) : null}

      {openItems.map((item) => {
        const working = busy[item.id] === true;
        return (
          <section className="card rv-card" key={item.id} aria-busy={working}>
            <p className="rv-source">
              {item.meeting.title} <span aria-hidden="true">·</span> {formatDay(item.meeting.created_at)}
            </p>
            <p className="rv-text">{item.text}</p>
            {item.heard_as !== null ? (
              <div className="rv-fix">
                <span className="rv-fix-label">
                  Heard as <strong>{item.heard_as}</strong>. Correct spelling:
                </span>
                <Field
                  id={`fix-${item.id}`}
                  label={`Correct spelling of ${item.heard_as}`}
                  hideLabel
                  maxLength={100}
                  autoComplete="off"
                  placeholder="e.g. company name"
                  error={fixErrors[item.id] || undefined}
                  value={fixes[item.id] ?? ""}
                  onChange={(event) => setFixes((current) => ({ ...current, [item.id]: event.target.value }))}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      void addTerm(item);
                    }
                  }}
                />
                <Button size="sm" type="button" disabled={working} onClick={() => void addTerm(item)}>
                  Add to vocabulary
                </Button>
              </div>
            ) : null}
            {errors[item.id] ? <Alert kind="error">{errors[item.id]}</Alert> : null}
            <div className="rv-actions">
              <Button kind="secondary" size="sm" href={`/meetings/${item.meeting.id}?tab=transcript`}>
                Open transcript
              </Button>
              <Button kind="secondary" size="sm" type="button" disabled={working} onClick={() => void check(item)}>
                <Icon name="check" />
                Mark as checked
              </Button>
            </div>
          </section>
        );
      })}

      {openItems.length === 0 ? (
        <div className="empty-box">
          {nothingAtAll ? (
            <EmptyState title="Nothing to review yet.">
              Parts of a transcript that may have been misheard appear here after each meeting. Meetings from before this
              feature were not scanned.
            </EmptyState>
          ) : (
            <EmptyState title="All caught up">New items appear here after each meeting.</EmptyState>
          )}
        </div>
      ) : null}

      {checkedItems.length > 0 ? (
        <div className="rv-done">
          <button
            className="rv-toggle"
            type="button"
            aria-expanded={showChecked}
            aria-controls="checked-items"
            onClick={() => setShowChecked((value) => !value)}
          >
            Checked ({checkedItems.length})
            <span className={showChecked ? "rv-chevron rv-chevron-open" : "rv-chevron"}>
              <Icon name="down" size={14} />
            </span>
          </button>
          {showChecked ? (
            <ul className="rv-done-list" id="checked-items">
              {checkedItems.map((item) => (
                <li className="rv-done-row" key={item.id}>
                  <div className="rv-done-body">
                    <p className="rv-done-text">{item.text}</p>
                    <p className="rv-done-source">
                      {item.meeting.title} <span aria-hidden="true">·</span> {formatDay(item.meeting.created_at)}
                    </p>
                    {errors[item.id] ? <p className="rv-done-error">{errors[item.id]}</p> : null}
                  </div>
                  <AppLink className="rv-done-link" href={`/meetings/${item.meeting.id}?tab=transcript`}>
                    Open transcript
                  </AppLink>
                  <Button kind="quiet" type="button" disabled={busy[item.id] === true} onClick={() => void undo(item)}>
                    Undo
                  </Button>
                </li>
              ))}
            </ul>
          ) : null}
          {showChecked && checked.truncated ? (
            <p className="meta">Showing the 200 newest checked items.</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
