"use client";

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Alert, AppLink, Button, EmptyState, Field, Icon, PageHead } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { ReviewItem, ReviewList } from "@/lib/api/types";
import { LocalTime } from "@/components/app/local-time";

const NOT_THERE = "This item is no longer available. Its meeting was deleted.";
const REWRITE_SLOW =
  "This is taking longer than expected. The rewrite may still finish: open the notes in a few minutes to see.";

// Newest meeting first, then the notes' order inside a meeting: the order the API lists them in.
function ordered(items: ReviewItem[]): ReviewItem[] {
  return [...items].sort((a, b) => b.meeting.id - a.meeting.id || a.id - b.id);
}

// After a term is added: offer to rewrite that meeting's notes with the corrected spelling. Adding a
// term does not change the notes already written (docs/fe-integration-guide-ai-workspace.md section 6).
// One offer at a time, as the designer draws it: a new one replaces the one on screen.
type Offer = {
  meetingId: number;
  // The meeting's title up to the first comma, as the designer's text names it ("Zoom 88205679674").
  meetingTitle: string;
  term: string;
  state: "offer" | "rewriting" | "done" | "failed";
  message: string | null;
};

// A row's own error: the API's words, or the designer's "no longer available" line (`gone`), which
// comes with "Remove from list".
type RowError = { text: string; gone: boolean };

// The Review screen: parts of a transcript that may have been misheard. An item can be checked (and
// unchecked), and one the model judged to be a misheard name or term (`heard_as` is not null) takes
// the corrected spelling into the vocabulary. The sidebar badge is `badges.review_open`, so after
// every change the server render is asked again (router.refresh keeps what is typed here).
//
// The designer's states: "Loading review items..." while the lists are being fetched, the red
// "Could not load review items" line with Try again (also what the page draws when the first load
// failed: both lists are null), and the dashed empty boxes.
export function ReviewView({ open: firstOpen, checked: firstChecked }: { open: ReviewList | null; checked: ReviewList | null }) {
  const router = useRouter();
  const [open, setOpen] = useState<ReviewList | null>(firstOpen);
  const [openItems, setOpenItems] = useState(firstOpen?.items ?? []);
  const [checkedItems, setCheckedItems] = useState(firstChecked?.items ?? []);
  const [checkedTruncated, setCheckedTruncated] = useState(firstChecked?.truncated ?? false);
  const [status, setStatus] = useState<"ready" | "loading" | "failed">(firstOpen && firstChecked ? "ready" : "failed");
  const [showChecked, setShowChecked] = useState(false);
  const [busy, setBusy] = useState<Record<number, boolean>>({});
  const busyRef = useRef(new Set<number>());
  const [errors, setErrors] = useState<Record<number, RowError>>({});
  const [fixes, setFixes] = useState<Record<number, string>>({});
  const [fixErrors, setFixErrors] = useState<Record<number, string>>({});
  const [offer, setOffer] = useState<Offer | null>(null);

  const note = (id: number, error: RowError | null) =>
    setErrors((current) => {
      const next = { ...current };
      if (error === null) delete next[id];
      else next[id] = error;
      return next;
    });

  const reload = useCallback(async () => {
    setStatus("loading");
    try {
      const [o, c] = await Promise.all([api.review.list("open"), api.review.list("checked")]);
      setOpen(o);
      setOpenItems(o.items);
      setCheckedItems(c.items);
      setCheckedTruncated(c.truncated);
      setErrors({});
      setStatus("ready");
      router.refresh();
    } catch {
      setStatus("failed");
    }
  }, [router]);

  // "Remove from list": the item is gone on the server, so only the screen forgets it.
  const forget = (id: number) => {
    setOpenItems((current) => current.filter((i) => i.id !== id));
    setCheckedItems((current) => current.filter((i) => i.id !== id));
    note(id, null);
    router.refresh();
  };

  // One request per item at a time; other items stay usable.
  async function run(id: number, work: () => Promise<void>) {
    if (busyRef.current.has(id)) return;
    busyRef.current.add(id);
    setBusy((current) => ({ ...current, [id]: true }));
    note(id, null);
    setFixErrors((current) => ({ ...current, [id]: "" }));
    try {
      await work();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        // Gone for good (its meeting was deleted): the row says so and offers to drop it.
        note(id, { text: NOT_THERE, gone: true });
      } else {
        note(id, { text: errorMessage(caught), gone: false });
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

  function offerRewrite(item: ReviewItem, term: string) {
    setOffer({
      meetingId: item.meeting.id,
      meetingTitle: item.meeting.title.split(",")[0] || "this meeting",
      term,
      state: "offer",
      message: null,
    });
  }

  // The term is in the vocabulary now: an item that comes back to open (Undo) starts with an empty box.
  const clearFix = (id: number) => setFixes((current) => ({ ...current, [id]: "" }));

  const addTerm = (item: ReviewItem) => {
    const term = (fixes[item.id] ?? "").trim();
    if (!term) {
      setFixErrors((current) => ({ ...current, [item.id]: "Type the correct spelling first." }));
      return Promise.resolve();
    }
    return run(item.id, async () => {
      try {
        moveToChecked((await api.review.addToVocabulary(item.id, term)).item);
        clearFix(item.id);
        offerRewrite(item, term);
      } catch (caught) {
        if (caught instanceof ApiError && caught.code === "duplicate_term") {
          // The term is there already: the designer's red line on the row. (The API still marks the
          // item checked, so it is under Checked after a reload.)
          setFixErrors((current) => ({ ...current, [item.id]: `\u201c${term}\u201d is already in your vocabulary.` }));
        } else if (caught instanceof ApiError && caught.code === "invalid_term") {
          setFixErrors((current) => ({ ...current, [item.id]: caught.message }));
        } else {
          throw caught;
        }
      }
    });
  };

  const patchOffer = (patch: Partial<Offer>) => setOffer((current) => (current ? { ...current, ...patch } : current));

  // Rewrite with the template the meeting already uses. The call waits for the model (minutes), the
  // same call the meeting page's "Rewrite notes" makes.
  async function rewrite(o: Offer) {
    patchOffer({ state: "rewriting", message: null });
    try {
      const meeting = await api.meetings.get(o.meetingId);
      if (!meeting.can_regenerate) {
        patchOffer({ state: "failed", message: "These notes cannot be rewritten right now." });
        return;
      }
      await api.meetings.regenerate(o.meetingId, meeting.template_id);
      patchOffer({ state: "done" });
      router.refresh();
    } catch (caught) {
      const slow = caught instanceof ApiError && (caught.code === "gateway_error" || caught.code === "network_error");
      patchOffer({ state: "failed", message: slow ? REWRITE_SLOW : errorMessage(caught) });
    }
  }

  const nothingAtAll = openItems.length === 0 && checkedItems.length === 0;

  return (
    <div className="stack-md measure-wide">
      <PageHead
        title="Review"
        subtitle="Parts of your meeting transcripts that may have been misheard. Check them against the transcript and fix names in your vocabulary."
      />

      {status === "failed" ? (
        <div className="state-fail" role="alert">
          <span className="state-fail-text">
            <Icon name="info" />
            Could not load review items. Check your connection and try again.
          </span>
          <Button kind="secondary" size="sm" type="button" onClick={() => void reload()}>
            Try again
          </Button>
        </div>
      ) : status === "loading" ? (
        <p className="state-line" role="status">
          <Icon name="spinner" />
          Loading review items...
        </p>
      ) : nothingAtAll ? (
        <div className="empty-box">
          <EmptyState title="Nothing to review yet.">
            Words that may have been misheard in your meetings will appear here after each meeting ends.
          </EmptyState>
        </div>
      ) : (
        <>
          {offer ? (
            <Alert kind="info" role="status">
              <p className="alert-text">
                {offer.state === "done"
                  ? `The notes for ${offer.meetingTitle} now use \u201c${offer.term}.\u201d`
                  : offer.state === "rewriting"
                    ? `Rewriting the notes for ${offer.meetingTitle}...`
                    : `\u201c${offer.term}\u201d was added to your vocabulary. Rewrite the notes for ${offer.meetingTitle} so they use the new spelling?`}
              </p>
              {offer.state === "rewriting" ? (
                <p className="alert-text">This can take a few minutes; keep this page open.</p>
              ) : null}
              {offer.state === "offer" || offer.state === "failed" ? (
                <>
                  <p className="alert-text">
                    The new notes replace the old ones. Decisions you removed and items you checked may come back if the
                    wording changes.
                  </p>
                  {offer.message ? <p className="alert-text">{offer.message}</p> : null}
                  <div className="alert-actions">
                    <Button kind="secondary" size="sm" type="button" onClick={() => setOffer(null)}>
                      Not now
                    </Button>
                    <Button size="sm" type="button" onClick={() => void rewrite(offer)}>
                      Rewrite notes
                    </Button>
                  </div>
                </>
              ) : offer.state === "done" ? (
                <div className="alert-actions">
                  <Button kind="secondary" size="sm" type="button" onClick={() => setOffer(null)}>
                    Dismiss
                  </Button>
                </div>
              ) : null}
            </Alert>
          ) : null}

          {open?.truncated ? (
            <Alert kind="notice">Showing the 200 newest items. Check some off and reload the page to see the rest.</Alert>
          ) : null}

          {openItems.map((item) => {
            const working = busy[item.id] === true;
            const rowError = errors[item.id];
            const fixError = fixErrors[item.id] || "";
            return (
              <section className="card rv-card" key={item.id} aria-busy={working}>
                <p className="rv-source">
                  {item.meeting.title} <span aria-hidden="true">·</span> <LocalTime iso={item.meeting.created_at} kind="day" />
                </p>
                <p className="rv-text">{item.text}</p>
                {item.heard_as !== null ? (
                  <div className={fixError ? "rv-fix rv-bad" : "rv-fix"}>
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
                      value={fixes[item.id] ?? ""}
                      onChange={(event) => {
                        setFixes((current) => ({ ...current, [item.id]: event.target.value }));
                        setFixErrors((current) => ({ ...current, [item.id]: "" }));
                      }}
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
                {fixError || rowError ? (
                  <div className="rv-row-error" role="alert">
                    <span className="rv-row-error-text">
                      <Icon name="info" size={14} />
                      {fixError || rowError?.text}
                    </span>
                    {!fixError && rowError?.gone ? (
                      <button className="rv-row-error-link" type="button" onClick={() => forget(item.id)}>
                        Remove from list
                      </button>
                    ) : null}
                  </div>
                ) : null}
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
              <EmptyState title="All caught up">New items from your meetings appear here after each meeting.</EmptyState>
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
                      <Icon name="check" />
                      <div className="rv-done-body">
                        <p className="rv-done-text">{item.text}</p>
                        <p className="rv-done-source">
                          {item.meeting.title} <span aria-hidden="true">·</span>{" "}
                          <LocalTime iso={item.meeting.created_at} kind="day" /> <span aria-hidden="true">·</span>{" "}
                          <AppLink className="rv-done-link" href={`/meetings/${item.meeting.id}?tab=transcript`}>
                            Open transcript
                          </AppLink>
                        </p>
                        {errors[item.id] ? (
                          <p className="rv-done-error">
                            {errors[item.id].text}{" "}
                            {errors[item.id].gone ? (
                              <button className="rv-row-error-link" type="button" onClick={() => forget(item.id)}>
                                Remove from list
                              </button>
                            ) : null}
                          </p>
                        ) : null}
                      </div>
                      <Button kind="quiet" type="button" disabled={busy[item.id] === true} onClick={() => void undo(item)}>
                        Undo
                      </Button>
                    </li>
                  ))}
                </ul>
              ) : null}
              {showChecked && checkedTruncated ? <p className="meta">Showing the 200 newest checked items.</p> : null}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
