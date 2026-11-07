"use client";

import { useState } from "react";
import { TaskDrawer } from "@/components/app/task-drawer";
import { Alert, AppLink, Button, Icon, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { useToday } from "@/components/app/use-today";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { AgendaItem, CarriedQuestion, SeriesPage, Task, TaskPerson } from "@/lib/api/types";
import { durationLabel, initials, platformLabel, plural } from "@/lib/format";
import { avatarTone, dayLabel, isOverdue, personInitials, taskDueText, todayIn } from "@/lib/tasks";
import { LocalTime } from "@/components/app/local-time";

// A 422 is developer text for most calls (`errorMessage` hides it), but the agenda's own refusals are
// written for the person ("Write the item without a web address.").
function say(caught: unknown): string {
  return caught instanceof ApiError && caught.code === "invalid_agenda_item" ? caught.message : errorMessage(caught);
}

const AVATAR_TONES = ["avatar-a", "avatar-b", "avatar-c", "avatar-d", "avatar-e"] as const;

// The Series page (the designer's `isSeries` screen in `Beyric Note Taker.dc.html`), fed by
// GET /api/series/{id} (docs/fe-integration-guide-series.md). Every list is limited to what the
// viewer may read, so two teammates see different tasks, questions and past meetings: that is the
// server's rule, not a bug to correct here.
//
// The sprint line under "Next meeting" ("Opens Sprint 13") is `sprint_note`: written by the server with
// the rule of the one-hour reminder mail, null (nothing drawn) when the next meeting opens no sprint.
// "Send agenda to attendees" is switched off, with a title saying why, when no teammate would
// receive it (`agenda_recipients` 0). Titles, questions and agenda lines are plain text.
function TaskRow({
  task,
  today,
  finishedOn,
  onOpen,
}: {
  task: Task;
  today: string;
  finishedOn?: string;
  onOpen: () => void;
}) {
  const doing = task.status === "doing";
  const done = task.status === "done";
  return (
    <div
      className={done ? "sr-row sr-row-done" : "sr-row"}
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault();
          onOpen();
        }
      }}
    >
      {done ? <Icon name="check" size={16} stroke={2.2} /> : null}
      <div className="sr-row-main">
        <div className="sr-row-title">{task.title || "Untitled task"}</div>
        <div className={done ? "sr-row-sub" : isOverdue(task.due_date, today) ? "sr-row-sub tk-due-overdue" : task.due_date === today ? "sr-row-sub sr-row-sub-warn" : "sr-row-sub"}>
          {done ? `Done ${finishedOn ?? ""}`.trim() : taskDueText(task, today)}
        </div>
      </div>
      {!done ? <span className={doing ? "sr-pill sr-pill-doing" : "sr-pill"}>{doing ? "In progress" : "To do"}</span> : null}
      {task.assignee ? (
        <span title={task.assignee.name} className={`avatar sr-av ${avatarTone(task.assignee.user_id)}`}>
          {personInitials(task.assignee)}
        </span>
      ) : done ? (
        <span className="sr-noowner">No owner</span>
      ) : (
        <span className="tk-owner-pill">Needs owner</span>
      )}
    </div>
  );
}

export function SeriesView({ initial }: { initial: SeriesPage }) {
  const today = useToday();
  const [page, setPage] = useState(initial);
  const [toast, flash] = useToast();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<"add" | "send" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [people, setPeople] = useState<TaskPerson[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);

  const reload = async () => {
    try {
      setPage(await api.series.get(page.id));
    } catch {
      // the page stays as it was
    }
  };

  async function openTask(id: number) {
    setOpenId(id);
    if (people.length === 0) {
      try {
        setPeople((await api.tasks.list({ limit: 1 })).people);
      } catch {
        // the drawer still opens; the assignee list is just short
      }
    }
  }

  async function send() {
    if (busy) return;
    setBusy("send");
    setError(null);
    try {
      const sent = await api.series.sendAgenda(page.id);
      flash(`Agenda sent to ${plural(sent.count, "attendee")}`);
    } catch (caught) {
      setError(say(caught));
    }
    setBusy(null);
  }

  async function addItem() {
    const value = text.trim();
    if (!value || busy) return;
    setBusy("add");
    setError(null);
    try {
      const item: AgendaItem = await api.series.addAgenda(page.id, value);
      setPage((p) => ({ ...p, agenda: [...p.agenda, item] }));
      setText("");
    } catch (caught) {
      setError(say(caught));
    }
    setBusy(null);
  }

  async function removeItem(item: AgendaItem) {
    setError(null);
    try {
      await api.series.removeAgenda(page.id, item.id);
      setPage((p) => ({ ...p, agenda: p.agenda.filter((a) => a.id !== item.id) }));
    } catch (caught) {
      setError(say(caught));
    }
  }

  async function resolve(question: CarriedQuestion) {
    setError(null);
    try {
      await api.questions.update(question.id, { resolved: true });
      setPage((p) => ({ ...p, questions: p.questions.filter((q) => q.id !== question.id) }));
    } catch (caught) {
      setError(say(caught));
    }
  }

  const all = [...page.unfinished, ...page.finished];
  const current = all.find((t) => t.id === openId) ?? null;
  const replace = (next: Task) =>
    setPage((p) => ({
      ...p,
      unfinished: p.unfinished.map((t) => (t.id === next.id ? next : t)),
      finished: p.finished.map((t) => (t.id === next.id ? next : t)),
    }));
  const noSend = page.agenda_recipients === 0;
  // The score tiles: the open tasks whose day has passed (in the person's zone) are the Overdue
  // tile; the rest of the open ones are Still open.
  const overdue = page.unfinished.filter((t) => isOverdue(t.due_date, today)).length;

  return (
    <div className="stack-lg sr">
      <AppLink className="crumb" href="/">
        <Icon name="back" size={14} />
        All meetings
      </AppLink>
      <div>
        <h1 className="sr-h1">{page.title}</h1>
        <div className="sr-sub">{page.platform ? `${page.cadence} · ${platformLabel(page.platform)}` : page.cadence}</div>
      </div>
      <div role="status" aria-live="polite">
        {error ? <Alert kind="error">{error}</Alert> : null}
      </div>
      <div className="sr-grid">
        <section className="sr-main" aria-label="Next meeting and carry-over">
          <div className="sr-next">
            <div>
              {page.next_text ? (
                <>
                  <div className="sr-next-label">Next meeting</div>
                  <div className="sr-next-value">{page.next_text}</div>
                  {page.sprint_note ? <div className="sr-next-sprint">{page.sprint_note}</div> : null}
                </>
              ) : null}
            </div>
            <Button
              type="button"
              onClick={() => void send()}
              disabled={noSend || busy === "send"}
              title={noSend ? "Nobody on your team was in the last three meetings, so there is nobody to send it to." : undefined}
            >
              Send agenda to attendees
            </Button>
          </div>
          <div className="sr-body">
            <div className="sr-score">
              <div className="sr-tile">
                <div className="sr-tile-n sr-tile-done">{page.finished.length}</div>
                <div className="sr-tile-label">Done since last meeting</div>
              </div>
              <div className="sr-tile">
                <div className="sr-tile-n">{page.unfinished.length - overdue}</div>
                <div className="sr-tile-label">Still open</div>
              </div>
              <div className="sr-tile">
                <div className={overdue > 0 ? "sr-tile-n sr-tile-late" : "sr-tile-n"}>{overdue}</div>
                <div className="sr-tile-label">Overdue</div>
              </div>
            </div>
            <div>
              <h2 className="sr-h2">Unfinished from last time</h2>
              <div className="sr-note">Tasks from earlier meetings in this series that are not done yet.</div>
              {page.unfinished.length > 0 ? (
                <div className="sr-list">
                  {page.unfinished.map((task) => (
                    <TaskRow key={task.id} task={task} today={today} onOpen={() => void openTask(task.id)} />
                  ))}
                </div>
              ) : (
                <div className="sr-empty">Everything from last time is done.</div>
              )}
            </div>
            <div>
              <div className="sr-h2-row">
                <h2 className="sr-h2">Finished since last meeting</h2>
                {page.finished.length > 0 ? <span className="sr-count">{page.finished.length} done</span> : null}
              </div>
              <div className="sr-note">
                {page.finished_since_text
                  ? `Tasks from this series completed since ${page.finished_since_text}.`
                  : "Tasks from this series completed since the last meeting."}
              </div>
              {page.finished.length > 0 ? (
                <div className="sr-list">
                  {page.finished.map((task) => (
                    <TaskRow
                      key={task.id}
                      task={task}
                      today={today}
                      finishedOn={
                        task.done_at
                          ? (() => {
                              const day = todayIn(page.timezone, new Date(task.done_at));
                              return day === today ? "Today" : dayLabel(day);
                            })()
                          : undefined
                      }
                      onOpen={() => void openTask(task.id)}
                    />
                  ))}
                </div>
              ) : (
                <div className="sr-empty">Nothing finished yet. Completed tasks will show here.</div>
              )}
            </div>
            <div>
              <h2 className="sr-h2">Open questions carried over</h2>
              <div className="sr-note">Questions left open in earlier meetings.</div>
              <div className="sr-qs">
                {page.questions.map((question) => (
                  <div className="sr-q" key={question.id}>
                    <div className="sr-q-main">
                      <div className="sr-q-text">{question.text}</div>
                      <div className="sr-note sr-q-src">From {question.meeting?.title ?? "an earlier meeting"}</div>
                    </div>
                    <button
                      type="button"
                      className="sr-q-btn"
                      disabled={!question.can_update}
                      title={question.can_update ? undefined : "Only the person who recorded the meeting, or an owner or admin, can resolve this."}
                      onClick={() => void resolve(question)}
                    >
                      Mark resolved
                    </button>
                  </div>
                ))}
                {page.questions.length === 0 ? <div className="sr-empty">No open questions.</div> : null}
              </div>
            </div>
            <div>
              <h2 className="sr-h2 sr-h2-tight">Other agenda items</h2>
              <div className="sr-agenda">
                {page.agenda.map((item) => (
                  <div className="sr-agenda-row" key={item.id}>
                    <span className="sr-agenda-text">{item.text}</span>
                    {item.can_remove ? (
                      <button type="button" className="sr-x" aria-label={`Remove "${item.text}"`} onClick={() => void removeItem(item)}>
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
                          <path d="M6 6l12 12M18 6 6 18" />
                        </svg>
                      </button>
                    ) : null}
                  </div>
                ))}
              </div>
              <form
                className="sr-add"
                onSubmit={(event) => {
                  event.preventDefault();
                  void addItem();
                }}
              >
                <input
                  className="input sr-add-input"
                  value={text}
                  maxLength={200}
                  aria-label="New agenda item"
                  placeholder="Add an item for the next meeting"
                  onChange={(event) => setText(event.target.value)}
                />
                <Button type="submit" kind="secondary" disabled={busy === "add" || !text.trim()}>
                  Add
                </Button>
              </form>
            </div>
          </div>
        </section>
        <aside className="sr-side">
          <div className="sr-side-title">Past meetings</div>
          {page.past_meetings.map((meeting) => (
            <AppLink key={meeting.id} href={`/meetings/${meeting.id}`} className="sr-past">
              <div>
                <div className="sr-past-date">
                  <LocalTime iso={meeting.created_at} />
                </div>
                <div className="sr-past-sub">
                  {durationLabel(meeting.duration_seconds, false)} · {plural(meeting.tasks_count, "task")}
                </div>
              </div>
              <Icon name="chevron" size={14} />
            </AppLink>
          ))}
          {page.past_meetings.length === 0 ? <div className="sr-past-none">No meetings recorded in this series yet.</div> : null}
          <div className="sr-side-title sr-side-gap">Attendees</div>
          <div className="sr-people">
            {page.attendees.map((person, index) => (
              <div className="sr-person" key={person.name}>
                <div className={`avatar avatar-26 ${AVATAR_TONES[index % AVATAR_TONES.length]}`} aria-hidden="true">
                  {initials(person.name)}
                </div>
                {person.name}
              </div>
            ))}
          </div>
        </aside>
      </div>
      {current ? (
        <TaskDrawer
          task={current}
          people={people}
          today={today}
          onChange={replace}
          onDelete={(id) => {
            setPage((p) => ({
              ...p,
              unfinished: p.unfinished.filter((t) => t.id !== id),
              finished: p.finished.filter((t) => t.id !== id),
            }));
            setOpenId(null);
          }}
          onClose={() => {
            setOpenId(null);
            void reload();
          }}
        />
      ) : null}
      <Toast message={toast} />
    </div>
  );
}
