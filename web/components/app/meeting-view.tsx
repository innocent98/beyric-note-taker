"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { CopyButton } from "@/components/app/copy-button";
import { DownloadMenu } from "@/components/app/download-menu";
import { ActionTasks } from "@/components/app/action-tasks";
import { NotesHtml } from "@/components/app/notes-html";
import { OpenQuestions } from "@/components/app/open-questions";
import { RecurringHint } from "@/components/app/recurring-hint";
import { SeriesPicker } from "@/components/app/series-picker";
import { ShareDialog, sentToast } from "@/components/app/share-dialog";
import { LanguageSelect, TranslationBanner, useTranslation } from "@/components/app/translation";
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
  Toast,
  Toggle,
} from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { Meeting, NoteLanguage, RetryBlock, Share, Template, TeamMember } from "@/lib/api/types";
import { LocalTime } from "@/components/app/local-time";
import { MilestoneProposals, MilestoneRows, useMeetingMilestones } from "@/components/app/meeting-milestones";
import { DELETED_FLAG } from "@/lib/deleted-flag";
import { durationLabel, initials, platformLabel, plural } from "@/lib/format";

const AVATAR_TONES = ["avatar-a", "avatar-b", "avatar-c", "avatar-d", "avatar-e"] as const;

// What a failed meeting says about retrying, from `retry_block` (the same words as the Jinja page).
// The API's own `retry_block_message` is used when it is there; these cover the rest.
const RETRY_BLOCKED: Record<RetryBlock, string> = {
  audio_deleted: "This meeting can't be retried: the audio has been deleted and no transcript was saved.",
  no_speech: "This meeting can't be retried: no speech was found, so trying again would give the same result.",
  no_audio: "This meeting can't be retried: nothing was captured. Send the bot again with a new link.",
  not_failed: "This meeting can't be retried.",
};

type Busy = "cancel" | "end" | "retry" | "rewrite" | "delete" | "share" | null;


// While the notes are being read for decisions and review items, ask again every 5 seconds for up to
// 2 minutes, then stop and say so (docs/fe-integration-guide-ai-workspace.md section 2).
const EXTRACTION_POLL_MS = 5_000;
const EXTRACTION_POLLS = 24;

// A state sentence longer than this (the lobby and prompt texts) is a paragraph, not a heading.
const LONG_STATE_TEXT = 60;

// The meeting page. `initial` is the server render; from then on the state here is the truth: every
// action's response is the full meeting (no refetch needed), and while `refresh_after_seconds` is set
// (bot_sent, processing) the page asks for it again, as the Jinja page's 30-second refresh did.
export function MeetingView({
  initial,
  templates,
  initialTab = "notes",
  defaultLanguage = "en",
  teamSize = 0,
  members = [],
  initialShares = [],
}: {
  initial: Meeting;
  templates: Template[];
  initialTab?: "notes" | "transcript";
  // The account's default notes language (Settings): opened when that translation exists.
  defaultLanguage?: NoteLanguage;
  // How many people are on the owner's team (`GET /api/team` `member_count`; 0 when unknown). The
  // "Share with team" card is drawn only when there is someone to share with.
  teamSize?: number;
  // The owner's team, to give a participant an address when their name matches a member's name or
  // alias (a transcript has names only), and the meeting's shares for "Shared with ...".
  members?: TeamMember[];
  initialShares?: Share[];
}) {
  const router = useRouter();
  const [meeting, setMeeting] = useState(initial);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [tab, setTab] = useState<string>(initialTab);
  const [milestones, setMilestones] = useMeetingMilestones(meeting.id, meeting.status === "ready", meeting.extraction_status);
  const [extractionStalled, setExtractionStalled] = useState(false);
  const extractionPolls = useRef(0);
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // The template the notes were written with, or the first one when it was deleted since.
  const [rewriteId, setRewriteId] = useState(
    String(templates.some((t) => t.id === initial.template_id) ? initial.template_id : (templates[0]?.id ?? "")),
  );
  const busyRef = useRef<Busy>(null);
  const [shareOpen, setShareOpen] = useState(false);
  const [shares, setShares] = useState<Share[]>(initialShares);
  const [toast, flashToast] = useToast(2600);
  const translation = useTranslation(initial.id, initial.translations, defaultLanguage, meeting.notes);

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

  // "Shared with ...": ask again every 5 seconds while a share waits for its translation or has mail
  // not yet sent, for up to 5 minutes (docs/fe-integration-guide-share.md section 5).
  const sharePolls = useRef(0);
  const sharing = shares.some((s) => s.status === "waiting_translation" || s.recipients.some((r) => r.status === "pending"));
  useEffect(() => {
    if (!sharing) return;
    const timer = setInterval(() => {
      if (document.visibilityState !== "visible") return;
      sharePolls.current += 1;
      if (sharePolls.current > 60) return clearInterval(timer);
      void api.meetings.shares.list(initial.id).then(setShares, () => {});
    }, 5_000);
    return () => clearInterval(timer);
  }, [sharing, initial.id]);

  // The decisions and review items are read from the notes after they are written (and again after a
  // rewrite): `pending` and `running` both mean "not yet".
  const extracting =
    meeting.status === "ready" && (meeting.extraction_status === "pending" || meeting.extraction_status === "running");
  useEffect(() => {
    if (!extracting) return;
    const tick = () => {
      if (document.visibilityState !== "visible" || busyRef.current !== null) return;
      extractionPolls.current += 1;
      if (extractionPolls.current >= EXTRACTION_POLLS) {
        clearInterval(timer);
        setExtractionStalled(true);
      }
      void refetch();
    };
    const timer = setInterval(tick, EXTRACTION_POLL_MS);
    return () => clearInterval(timer);
  }, [extracting, refetch]);

  async function act(kind: Exclude<Busy, null>, call: () => Promise<Meeting>) {
    if (busyRef.current !== null) return;
    extractionPolls.current = 0;
    setExtractionStalled(false);
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

  // Delete (lead-designed, not in the designer's file): 204 goes to the list with a toast. A 404 means
  // it is already gone (a second tab, a double click), which is the same outcome. 409 and 502 show
  // their own words and keep the page.
  async function remove() {
    if (busyRef.current !== null) return;
    busyRef.current = "delete";
    setBusy("delete");
    setError(null);
    setNotice(null);
    try {
      await api.meetings.remove(initial.id);
    } catch (caught) {
      if (!(caught instanceof ApiError && caught.status === 404)) {
        setError(errorMessage(caught));
        if (caught instanceof ApiError && caught.status === 409) void refetch();
        setConfirmingDelete(false);
        busyRef.current = null;
        setBusy(null);
        return;
      }
    }
    try {
      sessionStorage.setItem(DELETED_FLAG, "1");
    } catch {
      // Private mode: the list is shown without the toast.
    }
    router.push("/");
  }

  const m = meeting;
  // The name now, or the name it had when it was deleted (the API keeps it on the meeting).
  const templateName = templates.find((t) => t.id === m.template_id)?.name ?? m.template_name;
  const working = m.status === "bot_sent" || m.status === "processing";
  const tabs = [{ id: "notes", label: "Notes" }, ...(m.transcript ? [{ id: "transcript", label: "Transcript" }] : [])];
  // The words on the page: the translation's when one is shown, else the original's.
  const shownNotes = translation.translated?.notes ?? m.notes;
  const shownTranscript = translation.translated?.transcript ?? null;
  const duration = durationLabel(m.duration_seconds, working);
  // Everyone a share was (or is being) mailed to, by name or address, once each. Not the ones whose
  // mail did not go.
  const sharedWith = [
    ...new Set(
      shares.flatMap((s) =>
        s.status === "failed" ? [] : s.recipients.filter((r) => r.status !== "failed" && r.status !== "not_sent").map((r) => r.name ?? r.email),
      ),
    ),
  ];
  const retryText = m.retry_block !== null ? (m.retry_block_message ?? RETRY_BLOCKED[m.retry_block]) : null;

  return (
    <div className="stack-lg">
      <AppLink className="crumb" href="/">
        <Icon name="back" size={14} />
        All meetings
      </AppLink>
      <div className="detail-bar">
        <div className="detail-head">
          <div className="detail-title">
            <h1>{m.title}</h1>
            <Badge tone={m.status_tone} dot live>
              {m.status_text}
            </Badge>
          </div>
          <p className="meta-line">
            <span>
              <LocalTime iso={m.created_at} />
            </span>
            <span>{platformLabel(m.platform)}</span>
            <span>{duration}</span>
          </p>
        </div>
        {m.status !== "failed" ? (
          <DownloadMenu
            meetingId={m.id}
            title={m.title}
            ready={m.status === "ready" && Boolean(m.notes)}
            hasTranscript={Boolean(m.transcript)}
            language={translation.shown}
            onNotReady={() => void refetch()}
            trailing={
              m.status === "ready" && m.notes && m.can_share ? (
                <Button size="md" type="button" onClick={() => setShareOpen(true)}>
                  <Icon name="send" size={15} />
                  Share notes
                </Button>
              ) : null
            }
          />
        ) : null}
      </div>

      {m.worker_stale ? (
        <Alert kind="notice" role="alert">
          The background worker has not checked in for over 20 minutes. This meeting will not move forward until it is
          running again.
        </Alert>
      ) : null}
      {error ? <Alert kind="error">{error}</Alert> : null}
      {translation.error ? <Alert kind="error">{translation.error}</Alert> : null}
      {notice ? <Alert kind="info">{notice}</Alert> : null}

      {m.status === "bot_sent" && m.join_retry_at !== null ? (
        <Alert kind="notice">
          Google would not let the bot ask to join, so it was taken out. A new bot will be sent at about{" "}
          <LocalTime iso={m.join_retry_at} /> (attempt 2 of 2).
        </Alert>
      ) : m.status === "bot_sent" && m.join_attempts > 1 ? (
        <Alert kind="notice">
          {m.platform === "zoom"
            ? `Zoom turned the bot away, so a new one was sent (attempt ${m.join_attempts} of ${m.max_join_attempts}).`
            : `Google turned the bot away, so a new one was sent (attempt ${m.join_attempts}).`}
        </Alert>
      ) : null}
      {m.status === "ready" && m.bot_left_early ? (
        <Alert kind="notice">The note taker left the meeting early or was removed. The notes may be incomplete.</Alert>
      ) : null}

      {m.status === "ready" ? (
        <>
        {m.suggest_recurring && !m.series ? <RecurringHint meetingId={m.id} onDone={() => void refetch()} /> : null}
        <div className="detail-grid">
          <section className="card card-flush detail-main">
            <div className="panel-bar">
              {tabs.length > 1 ? (
                <TabButtons items={tabs} defaultId={initialTab} onChange={setTab} label="Meeting content" />
              ) : (
                <h2 className="panel-title">Notes</h2>
              )}
              <div className="panel-tools">
                <LanguageSelect value={translation.lang} onChange={translation.choose} />
                {tab === "transcript" && m.transcript ? (
                  <CopyButton key="transcript" text={shownTranscript ?? m.transcript} label="Copy transcript" />
                ) : (
                  <CopyButton key="notes" text={shownNotes ?? ""} label="Copy notes" />
                )}
              </div>
            </div>
            <TranslationBanner view={translation} />
            {tab === "transcript" && m.transcript ? (
              <div role="tabpanel" id="panel-transcript" aria-labelledby="tab-transcript" lang={translation.shown}>
                <TranscriptPanel text={shownTranscript ?? m.transcript} incomplete={m.speakers_incomplete} />
              </div>
            ) : (
              <div role="tabpanel" id="panel-notes" aria-labelledby="tab-notes" lang={translation.shown}>
                <NotesHtml
                  html={translation.translated?.notes_html ?? m.notes_html ?? ""}
                  actions={
                    !translation.translated && m.tasks.length > 0 ? <ActionTasks key={m.tasks.map((t) => t.id).join()} initial={m.tasks} /> : undefined
                  }
                  questions={
                    !translation.translated && m.series && m.open_questions.length > 0 ? (
                      <OpenQuestions key={m.open_questions.map((q) => q.id).join()} initial={m.open_questions} heading="Open questions" />
                    ) : undefined
                  }
                />
              </div>
            )}
          </section>
          <aside className="detail-side">
            <Card compact>
              <h2 className="side-title">Details</h2>
              <dl className="details">
                <dt>Date</dt>
                <dd>
                  <LocalTime iso={m.created_at} />
                </dd>
                <dt>Platform</dt>
                <dd>{platformLabel(m.platform)}</dd>
                <dt>Duration</dt>
                <dd>{duration}</dd>
                {m.shared_by ? (
                  <>
                    <dt>Shared by</dt>
                    <dd>{m.shared_by}</dd>
                  </>
                ) : null}
                {templateName ? (
                  <>
                    <dt>Template</dt>
                    <dd>{templateName}</dd>
                  </>
                ) : null}
                {m.series || (m.can_share && m.status === "ready") ? (
                  <>
                    <dt>Series</dt>
                    <dd className="det-series">
                      {m.series ? (
                        <AppLink className="det-link" href={`/series/${m.series.id}`}>
                          {m.series.title}
                        </AppLink>
                      ) : null}
                      {/* Lead-designed, not in the designer's file: "Add to recurring meeting" (spec 7). */}
                      {m.can_share && m.status === "ready" ? (
                        <button type="button" className="det-link det-link-quiet" data-dialog-open="series-dialog">
                          {m.series ? "Change" : "Add to recurring meeting"}
                        </button>
                      ) : null}
                    </dd>
                  </>
                ) : null}
                <MilestoneRows rows={milestones} />
              </dl>
              <MilestoneProposals
                rows={milestones}
                onConfirmed={(next) => setMilestones((all) => all.map((x) => (x.id === next.id ? next : x)))}
              />
              {m.participants.length > 0 || sharedWith.length > 0 ? (
                <>
                  <div className="side-rule" />
                  <h2 className="side-title">Participants</h2>
                  {sharedWith.length > 0 ? <p className="sh-shared">Shared with {sharedWith.join(", ")}</p> : null}
                  <div className="side-people">
                    {m.participants.map((name, index) => (
                      <div className="side-person" key={name}>
                        <div className={`avatar avatar-26 ${AVATAR_TONES[index % AVATAR_TONES.length]}`} aria-hidden="true">
                          {initials(name)}
                        </div>
                        {name}
                      </div>
                    ))}
                  </div>
                </>
              ) : null}
            </Card>
            {m.can_share && teamSize > 1 ? (
              <Card compact>
                {/* Lead-designed, not in the designer's file: the "Share with team" switch (spec 8.2). */}
                <div className="sh-row">
                  <div>
                    <h2 className="sh-title" id="share-team-label">
                      Share with team
                    </h2>
                    <p className="sh-sub" id="share-team-sub">
                      {m.shared_with_team
                        ? "Everyone on your team can read these notes and the transcript."
                        : "Private to you. Turn this on to let your team read the notes, transcript and tasks."}
                    </p>
                  </div>
                  <Toggle
                    checked={m.shared_with_team}
                    disabled={busy !== null}
                    aria-labelledby="share-team-label"
                    aria-describedby="share-team-sub"
                    onClick={() =>
                      void act("share", () => (m.shared_with_team ? api.meetings.unshare(m.id) : api.meetings.share(m.id)))
                    }
                  />
                </div>
              </Card>
            ) : null}
            <ExtractionCard
              status={m.extraction_status ?? null}
              stalled={extractionStalled}
              decisions={m.decisions_count}
              toCheck={m.review_open_count}
            />
            {m.can_regenerate ? (
              <Card compact>
                <form
                  className="stack-xs"
                  aria-busy={busy === "rewrite"}
                  onSubmit={(event) => {
                    event.preventDefault();
                    void act("rewrite", () => api.meetings.regenerate(m.id, Number(rewriteId)));
                  }}
                >
                  <div>
                    <h2 className="side-title">Rewrite notes</h2>
                    <p className="side-sub">Regenerate these notes with a different template.</p>
                    <p className="side-sub">Decisions you removed and items you checked may come back if the wording changes.</p>
                  </div>
                  <SelectField
                    id="template_id"
                    label="Rewrite the notes with a template"
                    hideLabel
                    compact
                    options={templates.map((t) => [t.id, t.name] as const)}
                    value={rewriteId}
                    disabled={busy !== null}
                    onChange={(event) => setRewriteId(event.target.value)}
                  />
                  <Button kind="secondary" size="md" disabled={busy !== null}>
                    {busy === "rewrite" ? "Rewriting notes..." : "Rewrite notes"}
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
        </>
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
              {m.status_text.length > LONG_STATE_TEXT ? (
                <p className="state-title">{m.status_text}</p>
              ) : (
                <h2 className="state-title">{m.status_text}</h2>
              )}
              <p>
                {m.status === "bot_sent"
                  ? "Notes and a full transcript will appear here when the meeting ends."
                  : "Notes and a full transcript will appear here when this finishes."}
              </p>
              {working ? <p className="muted">This page updates every 30 seconds.</p> : null}
              {m.can_end ? (
                <Button type="button" disabled={busy !== null} data-dialog-open="end-dialog">
                  {busy === "end" ? "Ending..." : "End now and write the notes"}
                </Button>
              ) : null}
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

      {m.can_delete ? (
        confirmingDelete ? (
          <div className="meeting-delete-ask" role="group" aria-label="Delete this meeting?">
            <p>
              <strong>Delete this meeting?</strong> This deletes the notes, the transcript, any translations, the
              decisions and review items taken from it, and the recording. This cannot be undone.
              {m.tasks_count > 0 ? ` This also deletes its ${plural(m.tasks_count, "task")}.` : ""}
            </p>
            <p>The hours it used stay counted.</p>
            <div className="dec-ask-actions">
              <Button kind="secondary" size="sm" type="button" disabled={busy !== null} onClick={() => setConfirmingDelete(false)}>
                Cancel
              </Button>
              <Button kind="danger" size="sm" type="button" disabled={busy !== null} onClick={() => void remove()}>
                {busy === "delete" ? "Deleting..." : "Delete meeting"}
              </Button>
            </div>
          </div>
        ) : (
          <div className="meeting-delete-row">
            <button type="button" className="meeting-delete" disabled={busy !== null} onClick={() => setConfirmingDelete(true)}>
              Delete meeting
            </button>
          </div>
        )
      ) : null}

      {m.can_share && m.status === "ready" && m.notes ? (
        <ShareDialog
          open={shareOpen}
          meetingId={m.id}
          title={m.title}
          participants={m.participants}
          members={members}
          hasTranscript={Boolean(m.transcript)}
          language={translation.shown}
          onClose={() => setShareOpen(false)}
          onShared={(created, lang, count) => {
            sharePolls.current = 0;
            flashToast(sentToast(created, lang, count));
            void api.meetings.shares.list(initial.id).then(setShares, () => {});
          }}
        />
      ) : null}
      <Toast message={toast} />
      <Dialog id="series-dialog" title="Add to recurring meeting">
        <SeriesPicker dialogId="series-dialog" meetingId={m.id} current={m.series ?? null} onDone={() => void refetch()} />
      </Dialog>

      <Dialog id="end-dialog" title="End the meeting and write the notes?">
        <p>
          The bot will leave the meeting now and the notes will be written from what it heard so far. Anything said
          after it leaves is not in the notes. To throw the recording away instead, cancel the meeting.
        </p>
        <div className="dialog-actions">
          <Button kind="secondary" type="button" data-dialog-close>
            Keep recording
          </Button>
          <Button type="button" data-dialog-close onClick={() => void act("end", () => api.meetings.end(m.id))}>
            End now and write the notes
          </Button>
        </div>
      </Dialog>

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

// The "Decisions and review" card: how many decisions this meeting has in the log and how many
// words are still to check, with a link to each screen. While the notes are still being read (or
// the reading failed) the counts are not known yet, so the card says that instead (words from
// before this card was drawn; a designer gap). null (not written yet) says nothing.
function ExtractionCard({
  status,
  stalled,
  decisions,
  toCheck,
}: {
  status: Meeting["extraction_status"] | null;
  stalled: boolean;
  decisions: number;
  toCheck: number;
}) {
  if (status === null) return null;
  if (status === "pending" || status === "running" || status === "failed") {
    const text =
      status === "failed"
        ? "The decisions are on the Decisions screen. The hints about names that may be misspelled could not be added. Rewrite the notes to try again."
        : stalled
          ? "Reading the notes is taking longer than usual. Decisions and Review will have this meeting when it finishes; this page has stopped checking, so reload it to look again."
          : "Reading the notes for decisions and items to check. They appear on the Decisions and Review screens in a moment.";
    return (
      <Card compact>
        <h2 className="side-title">Decisions and review</h2>
        <p className="side-sub" role={status === "failed" ? undefined : "status"}>
          {text}
        </p>
      </Card>
    );
  }
  const decisionLabel = decisions === 0 ? "No decisions" : plural(decisions, "decision");
  return (
    <Card compact>
      <div className="side-card">
        <h2 className="side-title">Decisions and review</h2>
        <div className="side-row">
          <div className="side-row-text">
            <span className="side-tile">
              <Icon name="check" size={15} stroke={2.4} />
            </span>
            <div>
              <div className="side-row-label">{decisionLabel}</div>
              <div className="side-row-sub">In the decisions log</div>
            </div>
          </div>
          <AppLink className="btn-link" href="/decisions">
            View
          </AppLink>
        </div>
        <div className="side-row">
          <div className="side-row-text">
            <span className={`side-tile ${toCheck > 0 ? "side-tile-warn" : "side-tile-quiet"}`}>
              <Icon name="eye" size={15} />
            </span>
            <div>
              <div className="side-row-label">{toCheck === 0 ? "Nothing to check" : `${toCheck} to check`}</div>
              <div className="side-row-sub">
                {toCheck === 0 ? "All review items are checked" : "Words that may have been misheard"}
              </div>
            </div>
          </div>
          <AppLink className="btn-link" href="/review">
            Review
          </AppLink>
        </div>
      </div>
    </Card>
  );
}
