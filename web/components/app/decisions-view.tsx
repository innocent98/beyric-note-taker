"use client";

import { useEffect, useRef, useState } from "react";
import { TaskDrawer } from "@/components/app/task-drawer";
import { useToday } from "@/components/app/use-today";
import { LocalTime } from "@/components/app/local-time";
import { Alert, AppLink, Button, EmptyState, Field, Icon, PageHead, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type {
  Decision,
  DecisionChanges,
  DecisionCounts,
  DecisionGroup,
  DecisionList,
  DecisionRef,
  DecisionStatus,
  Milestone,
  Project,
  Task,
  TaskPerson,
} from "@/lib/api/types";
import {
  isDueThisWeek,
  isSetAside,
  metaLine,
  movedCount,
  relatedTone,
  revisitBanner,
  searchFor,
  STATUSES,
  statusLabel,
  TABS,
  toneOf,
  transcriptHref,
} from "@/lib/decisions";
import { initials } from "@/lib/format";
import { loadSprintBook, type SprintBook } from "@/lib/sprints";
import { AVATAR_TONES, longDay } from "@/lib/tasks";

// How long typing pauses before the search is sent.
const SEARCH_DELAY_MS = 300;
// "Choosing Replaced offers a select of the person's other decisions (most recent 30)."
const REPLACED_CHOICES = 30;
const NO_COUNTS: DecisionCounts = { all: 0, approved: 0, proposed: 0, replaced: 0, rejected: 0 };

type Paging = { hasMore: boolean; before: number | null };
type Load = "idle" | "searching" | "failed";
type Tab = DecisionStatus | "all";

// The Decisions screen (the designer's, D:802-870): a record of what the meetings decided. Tabs
// (All, Approved, Proposed, Replaced, Rejected, each with its count over the whole log), a status
// pill on every row, and an open row with the Status, Revisit on and Decided by, the evidence with
// "Show in transcript", the related tasks, the Replaced by / Replaces links and "Remove from log".
// Only the person who recorded the meeting gets the Status select and the date field (`can_edit`);
// everyone else reads them as text. The first page is the server render. A tab or a search asks the
// API again, and older meetings come with before_id (docs/fe-integration-guide-decisions.md).
//
// The designer's states: "Loading decisions..." while a list is being fetched, the red "Could not
// load decisions" line with Try again (also what the page draws when the first load failed:
// `initial` is null), the dashed empty box, and a toast after a change or a removal.
//
// Lead-designed, not in the designer's file: the Revisit on field is a real date input (the file
// has a select of four fixed days) with a "No date" clear (U8); choosing Replaced offers an optional
// "Replaced by" select; a failed change says "Could not update the decision. Try again." in the
// page's error line; on a phone the tabs scroll sideways and the open row's panel is full width.
export function DecisionsView({ initial }: { initial: DecisionList | null }) {
  const today = useToday();
  const [groups, setGroups] = useState<DecisionGroup[]>(initial?.groups ?? []);
  const [counts, setCounts] = useState<DecisionCounts>(initial?.counts ?? NO_COUNTS);
  const [due, setDue] = useState(initial?.due_this_week ?? 0);
  const [paging, setPaging] = useState<Paging>({
    hasMore: initial?.has_more ?? false,
    before: initial?.next_before_id ?? null,
  });
  const [tab, setTab] = useState<Tab>("all");
  const [query, setQuery] = useState("");
  // The tab and search the list on screen answers (what the empty state names).
  const [shown, setShown] = useState<{ tab: Tab; query: string }>({ tab: "all", query: "" });
  const [load, setLoad] = useState<Load>(initial === null ? "failed" : "idle");
  const [toast, flash] = useToast();
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreFailed, setMoreFailed] = useState(false);
  // The decision whose row is open (one at a time, as the designer's list does) and the one whose
  // "Remove from log" is being asked about inside it.
  const [expanded, setExpanded] = useState<number | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);
  const [saving, setSaving] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The choices of the Replaced by select, read once when the first one is wanted.
  const [choices, setChoices] = useState<DecisionRef[] | null>(null);
  // The related task being looked at (the same drawer as the board).
  const [openTask, setOpenTask] = useState<Task | null>(null);
  const [people, setPeople] = useState<TaskPerson[]>([]);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [milestones, setMilestones] = useState<Milestone[]>([]);
  const [sprints, setSprints] = useState<SprintBook | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // Only the newest list may change the screen: a slow answer to an older one is dropped.
  const latest = useRef(0);

  useEffect(() => () => clearTimeout(timer.current), []);

  async function fetchList(next: Tab, text: string, open: number | null = null) {
    const mine = ++latest.current;
    setLoad("searching");
    setError(null);
    setConfirming(null);
    try {
      const page = await api.decisions.list({ q: text || undefined, status: next === "all" ? undefined : next });
      if (mine !== latest.current) return;
      setGroups(page.groups);
      setCounts(page.counts);
      setDue(page.due_this_week);
      setPaging({ hasMore: page.has_more, before: page.next_before_id });
      setShown({ tab: next, query: text });
      setMoreFailed(false);
      setExpanded(open !== null && page.groups.some((g) => g.decisions.some((d) => d.id === open)) ? open : null);
      setLoad("idle");
    } catch {
      if (mine !== latest.current) return;
      setLoad("failed");
    }
  }

  function onQuery(value: string) {
    setQuery(value);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => void fetchList(tab, value.trim()), SEARCH_DELAY_MS);
  }

  function pickTab(next: Tab) {
    if (next === tab) return;
    clearTimeout(timer.current);
    setTab(next);
    void fetchList(next, query.trim());
  }

  async function loadOlder() {
    if (paging.before === null || loadingMore) return;
    const mine = latest.current;
    setLoadingMore(true);
    setMoreFailed(false);
    try {
      const page = await api.decisions.list({
        q: shown.query || undefined,
        status: shown.tab === "all" ? undefined : shown.tab,
        before_id: paging.before,
      });
      // A tab or search that started meanwhile has replaced the list these older meetings belong to.
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

  function find(id: number): Decision | undefined {
    for (const group of groups) {
      const found = group.decisions.find((d) => d.id === id);
      if (found) return found;
    }
    return undefined;
  }

  async function remove(id: number) {
    if (removing !== null) return;
    const gone = find(id);
    setRemoving(id);
    setError(null);
    try {
      await api.decisions.hide(id);
      dropDecision(id);
      if (gone) {
        setCounts((c) => ({ ...c, all: Math.max(0, c.all - 1), [gone.status]: Math.max(0, c[gone.status] - 1) }));
        if (isDueThisWeek(gone, today)) setDue((n) => Math.max(0, n - 1));
      }
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

  // One PATCH of the fields that change. The answer is the whole decision and replaces the one on
  // screen; the other decision's "Replaces" line, the tab counts and the banner follow it.
  async function change(before: Decision, changes: DecisionChanges, toastText?: string) {
    if (saving !== null) return;
    setSaving(before.id);
    setError(null);
    try {
      const next = await api.decisions.update(before.id, changes);
      setGroups((current) => {
        const swapped = current.map((g) => ({
          ...g,
          decisions: g.decisions.map((d) => {
            if (d.id === next.id) return next;
            const kept = d.replaces.filter((r) => r.id !== next.id);
            const replaces = next.replaced_by?.id === d.id ? [...kept, { id: next.id, text: next.text }] : kept;
            return { ...d, replaces };
          }),
        }));
        // A tab shows one status: a decision that moved out of it leaves the list (the designer's list).
        if (shown.tab === "all" || next.status === shown.tab) return swapped;
        return swapped
          .map((g) => ({ ...g, decisions: g.decisions.filter((d) => d.id !== next.id) }))
          .filter((g) => g.decisions.length > 0);
      });
      setCounts((c) => movedCount(c, before.status, next.status));
      setDue((n) => Math.max(0, n + Number(isDueThisWeek(next, today)) - Number(isDueThisWeek(before, today))));
      if (shown.tab !== "all" && next.status !== shown.tab) setExpanded(null);
      if (toastText) flash(toastText);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        dropDecision(before.id);
        setExpanded(null);
      } else {
        setError("Could not update the decision. Try again.");
      }
    }
    setSaving(null);
  }

  async function wantChoices() {
    if (choices !== null) return;
    try {
      const page = await api.decisions.list({ limit: 10 });
      const flat = page.groups.flatMap((g) => g.decisions).map((d) => ({ id: d.id, text: d.text }));
      setChoices(flat.slice(0, REPLACED_CHOICES + 1));
    } catch {
      setChoices([]);
    }
  }

  // "Replaced by: ..." opens that decision with the tab on All and the search cleared: the API has no
  // call for one decision, so it is found by its words (the search is cut to what the API takes).
  function goTo(ref: DecisionRef) {
    clearTimeout(timer.current);
    const text = searchFor(ref.text);
    setTab("all");
    setQuery(text);
    void fetchList("all", text, ref.id);
  }

  async function open(id: number) {
    try {
      const task = await api.tasks.get(id);
      setOpenTask(task);
    } catch {
      setError("Could not open this task. Try again.");
      return;
    }
    if (people.length === 0) {
      try {
        setPeople((await api.tasks.list({ limit: 1 })).people);
      } catch {
        // the drawer still opens; the assignee list is just short
      }
      try {
        const read = (await api.projects.list()).projects;
        setProjects(read);
        setMilestones((await api.milestones.list()).milestones);
        setSprints(await loadSprintBook(api, read));
      } catch {
        // the Project, Milestone and Sprint rows are then left out of the drawer
      }
    }
  }

  // The drawer changed or deleted a task: the rows that list it follow.
  function taskChanged(task: Task) {
    setOpenTask(task);
    setGroups((current) =>
      current.map((g) => ({
        ...g,
        decisions: g.decisions.map((d) => ({
          ...d,
          related_tasks: d.related_tasks.map((t) => (t.id === task.id ? { ...t, title: task.title, status: task.status } : t)),
        })),
      })),
    );
  }

  const searching = load === "searching";
  const empty = groups.length === 0 && !paging.hasMore;
  const nothingAtAll = counts.all === 0 && shown.tab === "all" && shown.query === "";
  const showControls = load !== "failed" && !(nothingAtAll && load === "idle");

  return (
    <div className="stack-md measure-wide">
      <PageHead
        title="Decisions"
        subtitle="Decisions made in your meetings, with their status and the tasks that carry them out."
      />
      {showControls ? (
        <div className="dec-controls">
          <div className="dec-tabs" role="tablist" aria-label="Status">
            {TABS.map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                className={tab === value ? "dec-tab dec-tab-on" : "dec-tab"}
                aria-selected={tab === value}
                onClick={() => pickTab(value)}
              >
                {label}
                <span className="dec-tab-count">{counts[value]}</span>
              </button>
            ))}
          </div>
          <div className="dec-search" role="search">
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
        </div>
      ) : null}
      {showControls && due > 0 ? (
        <div className="dec-banner" role="status">
          <Icon name="calendar" size={15} />
          {revisitBanner(due)}
        </div>
      ) : null}
      {error ? <Alert kind="error">{error}</Alert> : null}

      {load === "failed" ? (
        <div className="state-fail" role="alert">
          <span className="state-fail-text">
            <Icon name="info" />
            Could not load decisions. Check your connection and try again.
          </span>
          <Button kind="secondary" size="sm" type="button" onClick={() => void fetchList(tab, query.trim())}>
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
                  const isOpen = expanded === decision.id;
                  const meta = metaLine(decision);
                  const speaker = decision.quote_speaker ?? decision.decided_by;
                  const busy = saving === decision.id;
                  return (
                    <li className="dec-row" key={decision.id}>
                      <button
                        type="button"
                        className="dec-toggle"
                        aria-expanded={isOpen}
                        onClick={() => {
                          setExpanded(isOpen ? null : decision.id);
                          setConfirming(null);
                        }}
                      >
                        <span className={`dec-pill dec-pill-${decision.status}`}>{statusLabel(decision.status)}</span>
                        <span className="dec-main">
                          <span className={isSetAside(decision.status) ? "dec-text dec-text-off" : "dec-text"}>{decision.text}</span>
                          {meta ? <span className="dec-meta">{meta}</span> : null}
                        </span>
                        <span className={isOpen ? "dec-chev dec-chev-open" : "dec-chev"}>
                          <Icon name="down" />
                        </span>
                      </button>
                      {isOpen ? (
                        <div className="dec-panel">
                          <div className="dec-fields">
                            <div className="dec-field">
                              <label htmlFor={`dec-status-${decision.id}`}>Status</label>
                              {decision.can_edit ? (
                                <select
                                  id={`dec-status-${decision.id}`}
                                  className="select td-select"
                                  value={decision.status}
                                  disabled={busy}
                                  onChange={(event) => {
                                    const status = event.target.value as DecisionStatus;
                                    if (status === "replaced") void wantChoices();
                                    void change(decision, { status }, `Status changed to ${statusLabel(status)}`);
                                  }}
                                >
                                  {STATUSES.map(([value, label]) => (
                                    <option key={value} value={value}>
                                      {label}
                                    </option>
                                  ))}
                                </select>
                              ) : (
                                <span className="dec-static">{statusLabel(decision.status)}</span>
                              )}
                            </div>
                            <div className="dec-field">
                              <label htmlFor={`dec-revisit-${decision.id}`}>Revisit on</label>
                              {decision.can_edit ? (
                                <div className="dec-date-wrap">
                                  <input
                                    id={`dec-revisit-${decision.id}`}
                                    className="input td-select dec-date-input"
                                    type="date"
                                    value={decision.revisit_on ?? ""}
                                    disabled={busy}
                                    onChange={(event) => void change(decision, { revisit_on: event.target.value || null })}
                                  />
                                  {decision.revisit_on ? (
                                    <button type="button" className="dec-clear" disabled={busy} onClick={() => void change(decision, { revisit_on: null })}>
                                      No date
                                    </button>
                                  ) : null}
                                </div>
                              ) : (
                                <span className="dec-static">{decision.revisit_on ? <LongDay iso={decision.revisit_on} /> : "No date"}</span>
                              )}
                            </div>
                            {decision.decided_by ? (
                              <div className="dec-field">
                                <span className="dec-label">Decided by</span>
                                <span className="dec-static">{decision.decided_by}</span>
                              </div>
                            ) : null}
                          </div>

                          {decision.status === "replaced" && decision.can_edit ? (
                            <div className="dec-field">
                              <label htmlFor={`dec-by-${decision.id}`}>Replaced by</label>
                              <select
                                id={`dec-by-${decision.id}`}
                                className="select td-select"
                                value={decision.replaced_by?.id ?? ""}
                                disabled={busy}
                                onFocus={() => void wantChoices()}
                                onChange={(event) =>
                                  void change(decision, { replaced_by_id: event.target.value ? Number(event.target.value) : null })
                                }
                              >
                                <option value="">No decision chosen</option>
                                {decision.replaced_by && !(choices ?? []).some((c) => c.id === decision.replaced_by?.id) ? (
                                  <option value={decision.replaced_by.id}>{decision.replaced_by.text}</option>
                                ) : null}
                                {(choices ?? [])
                                  .filter((c) => c.id !== decision.id)
                                  .slice(0, REPLACED_CHOICES)
                                  .map((c) => (
                                    <option key={c.id} value={c.id}>
                                      {c.text.length > 90 ? `${c.text.slice(0, 89)}…` : c.text}
                                    </option>
                                  ))}
                              </select>
                            </div>
                          ) : null}

                          {decision.quote ? (
                            <div className="dec-evidence">
                              {speaker ? (
                                <div className="dec-ev-who">
                                  <span className={`dec-ev-av ${AVATAR_TONES[toneOf(speaker) % AVATAR_TONES.length]}`}>{initials(speaker)}</span>
                                  <span className="dec-ev-name">{speaker}</span>
                                </div>
                              ) : null}
                              <div className="dec-ev-text">“{decision.quote}”</div>
                              {decision.line !== null ? (
                                <AppLink className="dec-ev-link" href={transcriptHref(group.meeting.id, decision.line)}>
                                  <Icon name="video" size={12} />
                                  Show in transcript
                                </AppLink>
                              ) : null}
                            </div>
                          ) : (
                            <div className="dec-ev-none">No transcript is available for this meeting.</div>
                          )}

                          {decision.related_tasks.length > 0 ? (
                            <div className="dec-related">
                              <span className="dec-label">Related tasks</span>
                              {decision.related_tasks.map((task) => {
                                const tone = relatedTone(task.status);
                                return (
                                  <button type="button" className="dec-rel" key={task.id} onClick={() => void open(task.id)}>
                                    <span className="dec-rel-main">
                                      <span className={`dec-rel-dot ${tone.dot}`} />
                                      {task.title || "Untitled task"}
                                    </span>
                                    <span className="dec-rel-status">{tone.label}</span>
                                  </button>
                                );
                              })}
                            </div>
                          ) : null}

                          {decision.replaced_by ? (
                            <div className="dec-link-line">
                              Replaced by:{" "}
                              <button type="button" className="dec-link" onClick={() => goTo(decision.replaced_by!)}>
                                {decision.replaced_by.text}
                              </button>
                            </div>
                          ) : null}
                          {decision.replaces.map((other) => (
                            <div className="dec-link-line" key={other.id}>
                              Replaces:{" "}
                              <button type="button" className="dec-link" onClick={() => goTo(other)}>
                                {other.text}
                              </button>
                            </div>
                          ))}

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
            nothingAtAll ? (
              <div className="empty-box">
                <EmptyState title="No decisions yet.">
                  Decisions made in your meetings will appear here after each meeting ends.
                </EmptyState>
              </div>
            ) : (
              <div className="empty-box dec-nomatch">No decisions match.</div>
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
      {openTask ? (
        <TaskDrawer
          task={openTask}
          people={people}
          today={today}
          projects={projects}
          milestones={milestones}
          sprints={sprints}
          onChange={taskChanged}
          onDelete={(id) => {
            setGroups((current) =>
              current.map((g) => ({
                ...g,
                decisions: g.decisions.map((d) => ({ ...d, related_tasks: d.related_tasks.filter((t) => t.id !== id) })),
              })),
            );
            setOpenTask(null);
          }}
          onClose={() => setOpenTask(null)}
        />
      ) : null}
      <Toast message={toast} />
    </div>
  );
}

// "Mon 30 Nov 2026": a day a person chose, as the designer writes it.
function LongDay({ iso }: { iso: string }) {
  return <time dateTime={iso}>{longDay(iso)}</time>;
}
