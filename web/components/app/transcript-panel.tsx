"use client";

import { useState } from "react";
import { LineEditor, RenameForm, type TranscriptEditor } from "@/components/app/transcript-edit";
import { Alert, IconButton } from "@/components/ui";
import { initials } from "@/lib/format";
import { parseTranscript } from "@/lib/transcript";

const TONES = ["avatar-a", "avatar-b", "avatar-c", "avatar-d"] as const;

// The transcript as the designer draws it: one row per turn with the speaker's initials. Text with
// no `[Name]:` labels (no speaker names yet) is shown as it is. Each row has the id `tl-N` (N from 1,
// the row's place in parseTranscript) so a link can scroll to it, and `highlight` gives row N the
// designer's pale yellow (D:287). A number outside the rows highlights nothing. No time is shown
// (none is stored).
//
// `editor` turns on edit mode (lead-designed, not in the designer's file): a Rename action beside
// each speaker and a pencil on each row. It is given only to someone who may edit, while the
// original-language transcript is shown. The rows themselves never move: editing changes a row's
// words or speaker, never their number or order, so `tl-N` and Ask Beyric's line numbers hold.
export function TranscriptPanel({
  text,
  incomplete,
  highlight = null,
  editor,
}: {
  text: string;
  incomplete: boolean;
  highlight?: number | null;
  editor?: TranscriptEditor;
}) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const lines = parseTranscript(text);
  const speakers = [...new Set(lines.map((l) => l.speaker).filter((s): s is string => s !== null))];
  return (
    <div className="transcript-body">
      {incomplete ? <Alert kind="notice">Speaker names unavailable for part of this transcript.</Alert> : null}
      {editor ? (
        <>
          <p className="side-sub transcript-edit-hint">
            Fix a speaker or a line. Lines can&apos;t be added, removed or split. The notes are not rewritten until you choose
            Rewrite notes.
          </p>
          {editor.error ? <Alert kind="error">{editor.error}</Alert> : null}
          <div className="transcript-speakers" role="group" aria-label="Speakers">
            {speakers.map((name) =>
              renaming === name ? (
                <RenameForm key={name} speaker={name} speakers={speakers} editor={editor} onClose={() => setRenaming(null)} />
              ) : (
                <span className="transcript-speaker" key={name}>
                  {name}
                  <button
                    type="button"
                    className="det-link det-link-quiet"
                    aria-label={`Rename speaker ${name}`}
                    disabled={editor.busy}
                    onClick={() => {
                      setEditing(null);
                      setRenaming(name);
                    }}
                  >
                    Rename
                  </button>
                </span>
              ),
            )}
          </div>
        </>
      ) : null}
      {lines.map((line, index) => {
        const open = editor && editing === index + 1;
        const body = open ? (
          <LineEditor
            key={`${index}:${line.speaker}:${line.text}`}
            line={index + 1}
            speaker={line.speaker}
            text={line.text}
            speakers={speakers}
            editor={editor}
            onClose={() => setEditing(null)}
          />
        ) : (
          <p className="transcript-text">{line.text}</p>
        );
        const pencil =
          editor && !open ? (
            <IconButton
              icon="pencil"
              label={`Edit line ${index + 1}`}
              tooltip="Edit line"
              disabled={editor.busy}
              onClick={() => {
                setRenaming(null);
                setEditing(index + 1);
              }}
            />
          ) : null;
        return (
          <div
            id={`tl-${index + 1}`}
            className={[
              "transcript-line",
              line.speaker === null ? "transcript-line-plain" : "",
              highlight === index + 1 ? "transcript-line-hl" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            key={index}
          >
            {line.speaker !== null ? (
              <>
                <div className={`avatar avatar-sm ${TONES[speakers.indexOf(line.speaker) % TONES.length]}`} aria-hidden="true">
                  {initials(line.speaker)}
                </div>
                <div>
                  {editor ? (
                    <div className="transcript-head">
                      <p className="transcript-who">{line.speaker}</p>
                      {pencil}
                    </div>
                  ) : (
                    <p className="transcript-who">{line.speaker}</p>
                  )}
                  {body}
                </div>
              </>
            ) : editor ? (
              <div className="transcript-head transcript-head-plain">
                <div className="transcript-plain-body">{body}</div>
                {pencil}
              </div>
            ) : (
              body
            )}
          </div>
        );
      })}
    </div>
  );
}
