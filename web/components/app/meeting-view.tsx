"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CopyButton } from "@/components/app/copy-button";
import { NotesHtml } from "@/components/app/notes-html";
import { TranscriptPanel } from "@/components/app/transcript-panel";
import {
  Alert,
  AppLink,
  Badge,
  Button,
  Card,
  Dialog,
  Icon,
  SelectField,
  TabButtons,
} from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { Meeting, RetryBlock, Template } from "@/lib/api/types";
import { formatTime, platformLabel } from "@/lib/format";

// What a failed meeting says about retrying, from `retry_block` (the same words as the Jinja page).
// The API's own `retry_block_message` is used when it is there; these cover the rest.
const RETRY_BLOCKED: Record<RetryBlock, string> = {
  audio_deleted: "This meeting can't be retried: the audio has been deleted and no transcript was saved.",
  no_speech: "This meeting can't be retried: no speech was found, so trying again would give the same result.",
  no_audio: "This meeting can't be retried: nothing was captured. Send the bot again with a new link.",
  not_failed: "This meeting can't be retried.",
};

type Busy = "cancel" | "retry" | "rewrite" | null;

// The meeting page. `initial` is the server render; from then on the state here is the truth: every
// action's response is the full meeting (no refetch needed), and while `refresh_after_seconds` is set
// (bot_sent, processing) the page asks for it again, as the Jinja page's 30-second refresh did.
export function MeetingView({ initial, templates }: { initial: Meeting; templates: Template[] }) {
  const [meeting, setMeeting] = useState(initial);
  const [tab, setTab] = useState("notes");
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [rewriteId, setRewriteId] = useState(String(initial.template_id));
  const busyRef = useRef<Busy>(null);

  const refetch = useCallback(async () => {
    try {
      setMeeting(await api.meetings.get(initial.id));
    } catch {
      // Keep what is on screen; a lost login has already sent the owner to /login.
    }
  }, [initial.id]);

  // Poll while the meeting is being worked on; pause while the tab is hidden and catch up when it
  // comes back; never overwrite the state under a running action.
  const interval = meeting.refresh_after_seconds;
  useEffect(() => {
    if (interval === null) return;
    const tick = () => {
      if (document.visibilityState === "visible" && busyRef.current === null) void refetch();
    };
    const timer = setInterval(tick, interval * 1000);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [interval, refetch]);

  async function act(kind: Exclude<Busy, null>, call: () => Promise<Meeting>) {
    if (busyRef.current !== null) return;
    busyRef.current = kind;
    setBusy(kind);
    setError(null);
    setNotice(null);
    try {
      setMeeting(await call());
    } catch (caught) {
      setError(errorMessage(caught));
      if (caught instanceof ApiError) {
        // The state moved under us (409), or the edge gave up on a slow rewrite and the rewrite
        // probably finished anyway (docs/fe-integration-guide-owner-app.md section 8, point 2).
        if (caught.status === 409) void refetch();
        if (caught.code === "gateway_error" || caught.code === "network_error") {
          setNotice("This is taking longer than expected. The change may still go through; this page checks again in a minute.");
          setTimeout(() => void refetch(), 60_000);
        }
      }
    }
    busyRef.current = null;
    setBusy(null);
  }

  const m = meeting;
  const templateName = templates.find((t) => t.id === m.template_id)?.name;
  const working = m.status === "bot_sent" || m.status === "processing";
  const tabs = [{ id: "notes", label: "Notes" }, ...(m.transcript ? [{ id: "transcript", label: "Transcript" }] : [])];
  const retryText = m.retry_block !== null ? (m.retry_block_message ?? RETRY_BLOCKED[m.retry_block]) : null;

  return (
    <div className="stack-lg">
      <div className="detail-head">
        <AppLink className="crumb" href="/">
          <Icon name="back" size={14} />
          All meetings
        </AppLink>
        <div className="detail-title">
          <h1>{m.title}</h1>
          <Badge tone={m.status_tone} dot>
            {m.status_text}
          </Badge>
        </div>
        <p className="meta-line">
          <span>{formatTime(m.created_at)}</span>
          <span>{platformLabel(m.platform)}</span>
        </p>
      </div>

      {error ? <Alert kind="error">{error}</Alert> : null}
      {notice ? <Alert kind="info">{notice}</Alert> : null}

      {m.status === "bot_sent" && m.join_attempts > 1 ? (
        <Alert kind="notice">
          Zoom turned the bot away, so a new one was sent (attempt {m.join_attempts} of {m.max_join_attempts}).
        </Alert>
      ) : null}
      {m.status === "ready" && m.bot_left_early ? (
        <Alert kind="notice">The note taker left the meeting early or was removed. The notes may be incomplete.</Alert>
      ) : null}

      {m.status === "ready" ? (
        <div className="detail-grid">
          <section className="card card-flush detail-main">
            <div className="panel-bar">
              {tabs.length > 1 ? (
                <TabButtons items={tabs} defaultId="notes" onChange={setTab} label="Meeting content" />
              ) : (
                <h2 className="panel-title">Notes</h2>
              )}
              {tab === "transcript" && m.transcript ? (
                <CopyButton text={m.transcript} label="Copy transcript" />
              ) : (
                <CopyButton text={m.notes ?? ""} label="Copy notes" />
              )}
            </div>
            {tab === "transcript" && m.transcript ? (
              <div role="tabpanel" id="panel-transcript" aria-labelledby="tab-transcript">
                <TranscriptPanel text={m.transcript} incomplete={m.speakers_incomplete} />
              </div>
            ) : (
              <div role="tabpanel" id="panel-notes" aria-labelledby="tab-notes">
                <NotesHtml html={m.notes_html ?? ""} />
              </div>
            )}
          </section>
          <aside className="detail-side">
            <Card>
              <h2 className="side-title">Details</h2>
              <dl className="details">
                <dt>Date</dt>
                <dd>{formatTime(m.created_at)}</dd>
                <dt>Platform</dt>
                <dd>{platformLabel(m.platform)}</dd>
                {templateName ? (
                  <>
                    <dt>Template</dt>
                    <dd>{templateName}</dd>
                  </>
                ) : null}
              </dl>
            </Card>
            {m.can_regenerate ? (
              <Card>
                <form
                  className="stack-sm"
                  aria-busy={busy === "rewrite"}
                  onSubmit={(event) => {
                    event.preventDefault();
                    void act("rewrite", () => api.meetings.regenerate(m.id, Number(rewriteId)));
                  }}
                >
                  <div>
                    <h2 className="side-title">Rewrite notes</h2>
                    <p className="side-sub">Regenerate these notes with a different template.</p>
                  </div>
                  <SelectField
                    id="template_id"
                    label="Rewrite the notes with a template"
                    hideLabel
                    options={templates.map((t) => [t.id, t.name] as const)}
                    value={rewriteId}
                    disabled={busy !== null}
                    onChange={(event) => setRewriteId(event.target.value)}
                  />
                  <Button kind="secondary" disabled={busy !== null}>
                    {busy === "rewrite" ? "Rewriting..." : "Rewrite notes"}
                  </Button>
                  {busy === "rewrite" ? (
                    <p className="side-sub" role="status">
                      Rewriting the notes. This can take a few minutes; keep this page open. The new notes replace the
                      old ones.
                    </p>
                  ) : null}
                </form>
              </Card>
            ) : null}
          </aside>
        </div>
      ) : (
        <section className="card state-card">
          <span className={`state-tile state-${m.status_tone}`} aria-hidden="true" />
          {m.status === "failed" ? (
            <>
              <h2 className="state-title">What went wrong</h2>
              <p>{m.failure_reason ?? "Something went wrong while processing this meeting."}</p>
              {m.can_retry ? (
                <Button type="button" disabled={busy !== null} onClick={() => void act("retry", () => api.meetings.retry(m.id))}>
                  {busy === "retry" ? "Trying again..." : "Try again"}
                </Button>
              ) : (
                <p className="muted">{retryText ?? RETRY_BLOCKED.no_audio}</p>
              )}
            </>
          ) : (
            <>
              <h2 className="state-title">
                {m.status === "bot_sent" ? "Waiting for the note taker to join" : m.status_text}
              </h2>
              <p>
                {m.status === "bot_sent"
                  ? "Admit the note taker from the waiting room if your meeting requires it."
                  : "Notes and a full transcript will appear here when this finishes."}
              </p>
              {working ? <p className="muted">This page updates every 30 seconds.</p> : null}
              {m.can_cancel ? (
                <Button kind="secondary" type="button" disabled={busy !== null} data-dialog-open="cancel-dialog">
                  Cancel and remove the bot
                </Button>
              ) : null}
            </>
          )}
        </section>
      )}

      {m.status !== "ready" && m.transcript ? (
        <section className="card card-flush">
          <div className="panel-bar">
            <h2 className="panel-title">Transcript</h2>
            <CopyButton text={m.transcript} label="Copy transcript" />
          </div>
          <TranscriptPanel text={m.transcript} incomplete={m.speakers_incomplete} />
        </section>
      ) : null}

      <Dialog id="cancel-dialog" title="Cancel this meeting?">
        <p>
          The bot is taken out of the meeting and nothing is recorded. This cannot be undone, and a cancelled meeting
          cannot be retried: send the bot again with the link instead.
        </p>
        <div className="dialog-actions">
          <Button kind="secondary" type="button" data-dialog-close>
            Keep waiting
          </Button>
          <Button type="button" data-dialog-close onClick={() => void act("cancel", () => api.meetings.cancel(m.id))}>
            Cancel and remove the bot
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
