"use client";

import { useEffect, useRef, useState } from "react";
import { Alert, AppLink, Button, EmptyState, Field, Icon, PageHead, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { DecisionGroup, DecisionList } from "@/lib/api/types";
import { LocalTime } from "@/components/app/local-time";

// How long typing pauses before the search is sent.
const SEARCH_DELAY_MS = 300;

type Paging = { hasMore: boolean; before: number | null };
type Load = "idle" | "searching" | "failed";

// The Decisions screen (the designer's, plus "Remove from log", which lives inside the open row): every decision of the owner's
// meetings grouped by meeting, newest meeting first. The first page is the server render. A search
// asks the API again (`q` matches the decision's words, or the meeting's title and code: a title
// match lists every decision of that meeting), and older meetings come with before_id
// (docs/fe-integration-guide-ai-workspace.md section 3). Removing hides a decision from the log and
// never edits the notes; there is no way to bring it back from here, so it asks first.
//
// The designer's states: "Loading decisions..." while a list is being fetched, the red "Could not
// load decisions" line with Try again (also what the page draws when the first load failed: `initial`
// is null), the dashed empty box, and a toast after a removal.
export function DecisionsView({ initial }: { initial: DecisionList | null }) {
  const [groups, setGroups] = useState<DecisionGroup[]>(initial?.groups ?? []);
  const [paging, setPaging] = useState<Paging>({
    hasMore: initial?.has_more ?? false,
    before: initial?.next_before_id ?? null,
  });
  const [query, setQuery] = useState("");
  // The search the list on screen answers (what the empty state names).
  const [shownQuery, setShownQuery] = useState("");
  const [load, setLoad] = useState<Load>(initial === null ? "failed" : "idle");
  const [toast, flash] = useToast();
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreFailed, setMoreFailed] = useState(false);
  // The decision whose row is open (one at a time, as the designer's list does) and the one whose
  // "Remove from log" is being asked about inside it.
  const [expanded, setExpanded] = useState<number | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // Only the newest search may change the screen: a slow answer to an older one is dropped.
  const latest = useRef(0);

  useEffect(() => () => clearTimeout(timer.current), []);

  async function search(text: string) {
    const mine = ++latest.current;
    setLoad("searching");
    setError(null);
    setConfirming(null);
    try {
      const page = await api.decisions.list({ q: text || undefined });
      if (mine !== latest.current) return;
      setGroups(page.groups);
      setPaging({ hasMore: page.has_more, before: page.next_before_id });
      setShownQuery(text);
      setMoreFailed(false);
      setLoad("idle");
    } catch {
      if (mine !== latest.current) return;
      setLoad("failed");
    }
  }

  function onQuery(value: string) {
    setQuery(value);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => void search(value.trim()), SEARCH_DELAY_MS);
  }

  async function loadOlder() {
    if (paging.before === null || loadingMore) return;
    const mine = latest.current;
    setLoadingMore(true);
    setMoreFailed(false);
    try {
      const page = await api.decisions.list({ q: shownQuery || undefined, before_id: paging.before });
      // A search that started meanwhile has replaced the list these older meetings belong to.
      if (mine === latest.current) {
        setGroups((current) => {
          const known = new Set(current.map((g) => g.meeting.id));
          return [...current, ...page.groups.filter((g) => !known.has(g.meeting.id))];
        });
        setPaging({ hasMore: page.has_more, before: page.next_before_id });
      }
    } catch {
      setMoreFailed(true);
    }
    setLoadingMore(false);
  }

  function dropDecision(id: number) {
    setGroups((current) =>
      current
        .map((g) => ({ ...g, decisions: g.decisions.filter((d) => d.id !== id) }))
        .filter((g) => g.decisions.length > 0),
    );
  }

  async function remove(id: number) {
    if (removing !== null) return;
    setRemoving(id);
    setError(null);
    try {
      await api.decisions.hide(id);
      dropDecision(id);
      setConfirming(null);
      setExpanded(null);
      flash("Decision removed from the log");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        // Already gone (a rewrite replaced it, or another tab removed it): gone either way.
        dropDecision(id);
        setConfirming(null);
        setExpanded(null);
      } else {
        setError(errorMessage(caught));
      }
    }
    setRemoving(null);
  }

  const searching = load === "searching";
  const empty = groups.length === 0 && !paging.hasMore;

  return (
    <div className="stack-md measure-wide">
      <PageHead title="Decisions" subtitle="Decisions made in your meetings, newest first." />
      <div role="search">
        <Field
          id="decision-search"
          label="Search decisions"
          hideLabel
          type="search"
          icon="search"
          maxLength={200}
          autoComplete="off"
          placeholder="Search decisions"
          value={query}
          onChange={(event) => onQuery(event.target.value)}
        />
      </div>
      {error ? <Alert kind="error">{error}</Alert> : null}

      {load === "failed" ? (
        <div className="state-fail" role="alert">
          <span className="state-fail-text">
            <Icon name="info" />
            Could not load decisions. Check your connection and try again.
          </span>
          <Button kind="secondary" size="sm" type="button" onClick={() => void search(query.trim())}>
            Try again
          </Button>
        </div>
      ) : searching ? (
        <p className="state-line" role="status">
          <Icon name="spinner" />
          Loading decisions...
        </p>
      ) : (
        <div className="stack-md">
          {groups.map((group) => (
            <section className="card card-flush" key={group.meeting.id}>
              <div className="dec-head">
                <div className="dec-head-text">
                  <span className="dec-date"><LocalTime iso={group.meeting.created_at} kind="day" /></span>
                  <h2 className="dec-title">{group.meeting.title}</h2>
                </div>
                <AppLink className="dec-open" href={`/meetings/${group.meeting.id}`}>
                  Open notes
                </AppLink>
              </div>
              <ul className="dec-list">
                {group.decisions.map((decision) => {
                  const open = expanded === decision.id;
                  return (
                    <li className="dec-row" key={decision.id}>
                      <button
                        type="button"
                        className="dec-toggle"
                        aria-expanded={open}
                        onClick={() => {
                          setExpanded(open ? null : decision.id);
                          setConfirming(null);
                        }}
                      >
                        <Icon name="check" />
                        <span className="dec-text">{decision.text}</span>
                        <span className={open ? "dec-chev dec-chev-open" : "dec-chev"}>
                          <Icon name="down" />
                        </span>
                      </button>
                      {open ? (
                        <div className="dec-panel">
                          {confirming === decision.id ? (
                            <div className="dec-ask" role="group" aria-label="Remove this decision from the log?">
                              <span className="dec-ask-text">Remove this decision from the log? It stays in the meeting notes.</span>
                              <div className="dec-ask-actions">
                                <Button kind="secondary" size="sm" type="button" disabled={removing !== null} onClick={() => setConfirming(null)}>
                                  Cancel
                                </Button>
                                <Button kind="danger" size="sm" type="button" disabled={removing !== null} onClick={() => void remove(decision.id)}>
                                  {removing === decision.id ? "Removing..." : "Remove"}
                                </Button>
                              </div>
                            </div>
                          ) : (
                            <button type="button" className="dec-remove" onClick={() => setConfirming(decision.id)}>
                              Remove from log
                            </button>
                          )}
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}

          {empty ? (
            shownQuery ? (
              <div className="empty-box dec-nomatch">No decisions match.</div>
            ) : (
              <div className="empty-box">
                <EmptyState title="No decisions yet.">
                  Decisions made in your meetings will appear here after each meeting ends.
                </EmptyState>
              </div>
            )
          ) : null}

          {paging.hasMore ? (
            <div className="load-more">
              <Button kind="secondary" size="sm" type="button" onClick={loadOlder} disabled={loadingMore}>
                {loadingMore ? "Loading..." : "Show older meetings"}
              </Button>
              {moreFailed ? <span className="meta">Could not load older meetings. Try again.</span> : null}
            </div>
          ) : null}
        </div>
      )}
      <Toast message={toast} />
    </div>
  );
}
