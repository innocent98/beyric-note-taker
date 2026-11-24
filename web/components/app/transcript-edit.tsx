"use client";

import { useState } from "react";
import { Alert, Button, Field, SelectField, TextareaField } from "@/components/ui";

// The transcript editor's two small forms (lead-designed: the designer's files have no editor).
// What a save does is the API's (docs/fe-integration-guide-transcript-edit.md): a name is cleaned
// there, a line is saved as one plain line, and the number and order of lines never change.

export const MAX_NAME = 80;
export const MAX_LINE = 5000;

export type TranscriptEditor = {
  busy: boolean;
  error: string | null;
  // Each answers true when the edit was saved (the form then closes) and false when it was not (the
  // form stays open, `error` says why).
  rename: (from: string, to: string, merge: boolean) => Promise<boolean>;
  editLine: (line: number, change: { text?: string; speaker?: string }) => Promise<boolean>;
};

// Rename one speaker everywhere. A name already in the meeting merges the two: asked first.
export function RenameForm({
  speaker,
  speakers,
  editor,
  onClose,
}: {
  speaker: string;
  speakers: string[];
  editor: TranscriptEditor;
  onClose: () => void;
}) {
  const [to, setTo] = useState(speaker);
  const [merging, setMerging] = useState(false);
  const name = to.trim();
  const exists = name !== speaker && speakers.includes(name);

  async function save(merge: boolean) {
    if (await editor.rename(speaker, name, merge)) onClose();
    else setMerging(false);
  }

  return (
    <form
      className="transcript-edit-form"
      aria-label={`Rename speaker ${speaker}`}
      onSubmit={(event) => {
        event.preventDefault();
        if (exists && !merging) setMerging(true);
        else void save(exists);
      }}
    >
      <Field
        id={`rename-${speaker}`}
        label={`New name for ${speaker}`}
        hideLabel
        value={to}
        maxLength={MAX_NAME}
        autoFocus
        disabled={editor.busy}
        onChange={(event) => {
          setTo(event.target.value);
          setMerging(false);
        }}
      />
      {merging ? (
        <Alert kind="notice">
          {name} is already in this meeting. Merge {speaker} into {name}? Every line from both will be labelled {name}.
        </Alert>
      ) : null}
      <div className="transcript-edit-row">
        <Button size="sm" disabled={editor.busy || name === "" || name === speaker}>
          {editor.busy ? "Saving..." : merging ? `Merge into ${name}` : "Rename"}
        </Button>
        <Button kind="secondary" size="sm" type="button" disabled={editor.busy} onClick={onClose}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

const SOMEONE_ELSE = "\u0000new";

// Change one line's words, its speaker, or both.
export function LineEditor({
  line,
  speaker,
  text,
  speakers,
  editor,
  onClose,
}: {
  line: number;
  speaker: string | null;
  text: string;
  speakers: string[];
  editor: TranscriptEditor;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(text);
  const [who, setWho] = useState(speaker ?? "");
  const [other, setOther] = useState("");
  const typedName = other.trim();
  const newSpeaker = who === SOMEONE_ELSE ? typedName : who;
  const textChanged = draft.trim() !== "" && draft.split(/\s+/).join(" ").trim() !== text.split(/\s+/).join(" ").trim();
  const speakerChanged = newSpeaker !== "" && newSpeaker !== (speaker ?? "");
  const ready = (textChanged || speakerChanged) && draft.trim() !== "" && (who !== SOMEONE_ELSE || typedName !== "");
  const options: Array<readonly [string, string]> = [
    ...(speaker === null ? ([["", "No speaker"]] as const) : []),
    ...speakers.map((name) => [name, name] as const),
    [SOMEONE_ELSE, "Someone else..."],
  ];

  async function save() {
    const change: { text?: string; speaker?: string } = {};
    if (textChanged) change.text = draft;
    if (speakerChanged) change.speaker = newSpeaker;
    if (await editor.editLine(line, change)) onClose();
  }

  return (
    <form
      className="transcript-edit-form"
      aria-label={`Edit line ${line}`}
      onSubmit={(event) => {
        event.preventDefault();
        if (ready) void save();
      }}
    >
      <TextareaField
        id={`line-text-${line}`}
        label={`Words of line ${line}`}
        hideLabel
        rows={3}
        value={draft}
        maxLength={MAX_LINE}
        autoFocus
        disabled={editor.busy}
        hint={text.includes("\n") ? "This line has more than one paragraph. Saving joins them into one." : undefined}
        onChange={(event) => setDraft(event.target.value)}
      />
      <SelectField
        id={`line-speaker-${line}`}
        label={`Speaker of line ${line}`}
        compact
        options={options}
        value={who}
        disabled={editor.busy}
        onChange={(event) => setWho(event.target.value)}
      />
      {who === SOMEONE_ELSE ? (
        <Field
          id={`line-new-speaker-${line}`}
          label="Name of the speaker"
          value={other}
          maxLength={MAX_NAME}
          disabled={editor.busy}
          onChange={(event) => setOther(event.target.value)}
        />
      ) : null}
      <div className="transcript-edit-row">
        <Button size="sm" disabled={editor.busy || !ready}>
          {editor.busy ? "Saving..." : "Save line"}
        </Button>
        <Button kind="secondary" size="sm" type="button" disabled={editor.busy} onClick={onClose}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
