"use client";

import { useEffect, useRef, useState } from "react";
import { Alert, Icon } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import { languageName } from "@/components/app/translation";
import type { ExportFormat, NoteLanguage } from "@/lib/api/types";

const FORMATS: { format: ExportFormat; tag: string; label: string; sub: string }[] = [
  { format: "pdf", tag: "PDF", label: "PDF", sub: "Best for reading and printing" },
  { format: "docx", tag: "DOCX", label: "Word document", sub: "Edit in Word or Google Docs" },
];
const NAME: Record<ExportFormat, string> = { pdf: "PDF", docx: "Word document" };
const DONE_SECONDS = 8;

type State =
  | { kind: "idle" }
  | { kind: "working"; format: ExportFormat }
  | { kind: "done"; filename: string }
  | { kind: "failed"; message: string };

// Hands the bytes to the browser as a download. An object URL, so the file never touches a server
// again: it was built for this click and is not stored.
function save(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

// Two siblings for the header's flex row (`.detail-bar`): the button (`.dl`) and, on a row of its
// own below, the answer to the last click (`.dl-status`).
// The Download menu of the meeting page (the designer's: PDF, Word, "Include the full transcript").
// `ready` is false while the notes are still being written: the button shows, disabled, with the
// reason. The file comes from GET /api/meetings/{id}/export; the answer to the click shows below
// the header: preparing, "<name> downloaded", or why it did not work.
export function DownloadMenu({
  meetingId,
  title,
  ready,
  hasTranscript,
  language = "en",
  onNotReady,
}: {
  meetingId: number;
  title: string;
  ready: boolean;
  hasTranscript: boolean;
  // The language the page is showing: a translation that is on screen is the one downloaded.
  language?: NoteLanguage;
  // The server said the notes are not ready (409): the page's own state is stale, so refetch it.
  onNotReady: () => void;
}) {
  const [state, setState] = useState<State>({ kind: "idle" });
  const [transcript, setTranscript] = useState(false);
  const menu = useRef<HTMLDetailsElement>(null);
  const running = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  async function start(format: ExportFormat) {
    if (running.current) return;
    running.current = true;
    clearTimeout(timer.current);
    if (menu.current) menu.current.open = false;
    setState({ kind: "working", format });
    try {
      const file = await api.meetings.export(meetingId, format, transcript && hasTranscript, language);
      const filename = file.filename ?? `${title}.${format}`;
      save(file.blob, filename);
      setState({ kind: "done", filename });
      timer.current = setTimeout(() => setState({ kind: "idle" }), DONE_SECONDS * 1000);
    } catch (caught) {
      setState({ kind: "failed", message: errorMessage(caught) });
      if (caught instanceof ApiError && caught.code === "not_ready") onNotReady();
    }
    running.current = false;
  }

  const working = state.kind === "working";
  return (
    <>
      <div className="dl">
      {ready ? (
        <details className="menu dl-menu" ref={menu}>
          <summary className="btn btn-secondary btn-md" aria-disabled={working} aria-haspopup="true">
            <Icon name="download" size={15} />
            {working ? `Preparing ${NAME[state.format]}...` : "Download"}
            <Icon name="down" size={14} />
          </summary>
          <div className="menu-panel dl-panel">
            {FORMATS.map((f) => (
              <button
                key={f.format}
                type="button"
                className="dl-option"
                disabled={working}
                onClick={() => void start(f.format)}
              >
                <span className={`dl-tile dl-tile-${f.format}`} aria-hidden="true">
                  {f.tag}
                </span>
                <span className="dl-text">
                  <span className="dl-label">{f.label}</span>
                  <span className="dl-sub">{f.sub}</span>
                </span>
              </button>
            ))}
            {hasTranscript ? (
              <>
                <hr className="dl-rule" />
                <label className="dl-check">
                  <input type="checkbox" checked={transcript} onChange={(event) => setTranscript(event.target.checked)} />
                  Include the full transcript
                </label>
              </>
            ) : null}
            <p className="dl-lang">In {languageName(language)}, with summary, decisions, action items and owners.</p>
          </div>
        </details>
      ) : (
        <>
          <button type="button" className="btn btn-secondary btn-md" disabled aria-describedby="dl-why">
            <Icon name="download" size={15} />
            Download
            <Icon name="down" size={14} />
          </button>
          <span id="dl-why" className="visually-hidden">
            Available when the notes are ready.
          </span>
        </>
      )}
      </div>
      {state.kind === "working" ? (
        <div className="dl-status">
          <Alert kind="info" role="status">
            Preparing {NAME[state.format]}... a long meeting can take a few seconds.
          </Alert>
        </div>
      ) : state.kind === "done" ? (
        <div className="dl-status">
          <Alert kind="info" role="status">
            {state.filename} downloaded
          </Alert>
        </div>
      ) : state.kind === "failed" ? (
        <div className="dl-status">
          <Alert kind="error">{state.message}</Alert>
        </div>
      ) : null}
    </>
  );
}
