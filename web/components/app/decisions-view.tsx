"use client";

import { useEffect, useRef, useState } from "react";
import { Alert, AppLink, Button, EmptyState, Field, Icon, PageHead } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { DecisionGroup, DecisionList } from "@/lib/api/types";
import { LocalTime } from "@/components/app/local-time";

// How long typing pauses before the search is sent.
const SEARCH_DELAY_MS = 300;

type Paging = { hasMore: boolean; before: number | null };
type Load = "idle" | "searching" | "failed";

// The Decisions screen (the designer's, plus "Remove from log"): every decision of the owner's
// meetings grouped by meeting, newest meeting first. The first page is the server render. A search
// asks the API again (`q` matches the decision's words, or the meeting's title and code: a title
// match lists every decision of that meeting), and older meetings come with before_id
// (docs/fe-integration-guide-ai-workspace.md section 3). Removing hides a decision from the log and
// never edits the notes; there is no way to bring it back from here, so it asks first.
export function DecisionsView({ initial }: { initial: DecisionList }) {
  const [groups, setGroups] = useState<DecisionGroup[]>(initial.groups);
  const [paging, setPaging] = useState<Paging>({ hasMore: initial.has_more, before: initial.next_before_id });
  const [query, setQuery] = useState("");
  // The search the list on screen answers (what the empty state names).
  const [shownQuery, setShownQuery] = useState("");
  const [load, setLoad] = useState<Load>("idle");
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreFailed, setMoreFailed] = useState(false);
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
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        // Already gone (a rewrite replaced it, or another tab removed it): gone either way.
        dropDecision(id);
        setConfirming(null);
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
      <PageHead title="Decisions" subtitle="Everything agreed in your meetings, newest first." />
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
        <Alert kind="error">
          <p className="alert-text">Could not load the decisions. Check your connection and try again.</p>
          <Button kind="secondary" size="sm" type="button" onClick={() => void search(query.trim())}>
            Try again
          </Button>
        </Alert>
      ) : (
        <div className="stack-md" aria-busy={searching}>
          {searching ? (
            <p className="meta" role="status">
              Searching...
            </p>
          ) : null}
          {groups.map((group) => (
            <section className={`card card-flush${searching ? " is-busy" : ""}`} key={group.meeting.id}>
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
                {group.decisions.map((decision) => (
                  <li className="dec-item" key={decision.id}>
                    <Icon name="check" />
                    <div className="dec-body">
                      <p className="dec-text">{decision.text}</p>
                      {confirming === decision.id ? (
                        <div className="dec-confirm" role="group" aria-label="Remove this decision from the log?">
                          <span>Remove it from this log? The notes keep it.</span>
                          <Button
                            kind="secondary"
                            size="sm"
                            type="button"
                            disabled={removing !== null}
                            onClick={() => void remove(decision.id)}
                          >
                            {removing === decision.id ? "Removing..." : "Remove"}
                          </Button>
                          <Button
                            kind="quiet"
                            type="button"
                            disabled={removing !== null}
                            onClick={() => setConfirming(null)}
                          >
                            Keep
                          </Button>
                        </div>
                      ) : null}
                    </div>
                    {confirming === decision.id ? null : (
                      <Button
                        kind="quiet"
                        type="button"
                        aria-label={`Remove from log: ${decision.text}`}
                        onClick={() => setConfirming(decision.id)}
                      >
                        Remove from log
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ))}

          {empty && !searching ? (
            <div className="empty-box">
              {shownQuery ? (
                <EmptyState title="No decisions match your search." />
              ) : (
                <EmptyState title="No decisions yet.">
                  Decisions from your meeting notes appear here, usually a minute after the notes are written.
                </EmptyState>
              )}
            </div>
          ) : null}

          {paging.hasMore ? (
            <div className="load-more">
              <Button kind="secondary" size="sm" type="button" onClick={loadOlder} disabled={loadingMore || searching}>
                {loadingMore ? "Loading..." : "Show older meetings"}
              </Button>
              {moreFailed ? <span className="meta">Could not load older meetings. Try again.</span> : null}
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
