"use client";

import { useRef, useState, type ChangeEvent } from "react";
import { GateText } from "@/components/billing/gate-text";
import { Alert, Button, Card, Icon, TextareaField } from "@/components/ui";
import { api } from "@/lib/api/browser";
import type { Brief, BriefImported } from "@/lib/api/types";
import { recipeErrorMessage } from "@/lib/recipes";

const number = (n: number) => n.toLocaleString("en-US");

// The team's brief: the reference text the model may use besides the meeting (services, prices, terms, house
// style). LEAD-DESIGNED (the designer's files draw none of recipes), built from the Templates editor's parts.
// Owners and admins write it; a member reads it (`team.can_manage`). "Import from a file" turns a Word, PDF,
// text or Markdown file into text and shows it for review: nothing is saved until the person puts it in the
// brief (Replace, or Add to the end) and presses Save. Everything the person sees as a refusal is the
// server's own sentence.
export function BriefEditor({ initial }: { initial: Brief }) {
  const [current, setCurrent] = useState(initial);
  const [text, setText] = useState(initial.text);
  const [review, setReview] = useState<BriefImported | null>(null);
  const [reviewText, setReviewText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const team = current.team;
  const over = text.length - current.max_chars;
  const dirty = text !== current.text;

  async function save() {
    if (busy || over > 0 || !dirty) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const result = await api.brief.save(text);
      setCurrent(result);
      setText(result.text); // what was kept, not what was typed
      setSaved(true);
    } catch (caught) {
      setError(recipeErrorMessage(caught));
    }
    setBusy(false);
  }

  async function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ""; // the same file can be chosen again
    if (!file || importing) return;
    setImporting(true);
    setError(null);
    setSaved(false);
    try {
      const result = await api.brief.importFile(file);
      setReview(result);
      setReviewText(result.text);
    } catch (caught) {
      setReview(null);
      setError(recipeErrorMessage(caught));
    }
    setImporting(false);
  }

  const put = (next: string) => {
    setText(next);
    setReview(null);
    setSaved(false);
  };

  return (
    <Card>
      <div className="stack-md brief-editor">
        <div>
          <h2 className="brief-title">Team brief</h2>
          <p className="field-hint">
            What Beyric may use besides the meeting: your services, prices, terms, house style, a past proposal.
            {" "}Everyone on {team.name} can read this, and it is used in the drafts they make.
          </p>
        </div>
        {error ? (
          <Alert kind="error">
            <GateText message={error} />
          </Alert>
        ) : null}
        <TextareaField
          id="brief-text"
          label="Team brief"
          hideLabel
          rows={7}
          readOnly={!team.can_manage}
          value={text}
          placeholder={team.can_manage ? "e.g. Audits from 400 USD. Retainers from 1,200 USD a month. Pay within 14 days." : ""}
          onChange={(event) => {
            setText(event.target.value);
            setSaved(false);
          }}
        />
        <div className="brief-meta field-hint">
          <span className={over > 0 ? "brief-over" : undefined} role="status">
            {over > 0
              ? `${number(text.length)} of ${number(current.max_chars)} characters. ${number(over)} too many.`
              : `${number(text.length)} of ${number(current.max_chars)} characters`}
          </span>
          {current.updated_by ? <span>Last saved by {current.updated_by}</span> : null}
        </div>
        {!team.can_manage ? (
          <p className="field-hint">Only an owner or admin of the team can change the brief.</p>
        ) : (
          <>
            {review ? (
              <fieldset className="brief-review" aria-label="Imported text">
                <legend className="field-label">Imported text</legend>
                <p className="field-hint">
                  Review it and change what you need. It is not saved yet.
                  {review.pages ? ` Read from a PDF of ${review.pages} ${review.pages === 1 ? "page" : "pages"}.` : ""}
                </p>
                {review.truncated ? (
                  <Alert kind="notice">Only the first {number(current.max_chars)} characters were imported.</Alert>
                ) : null}
                <textarea
                  className="textarea"
                  rows={8}
                  aria-label="Imported text"
                  value={reviewText}
                  onChange={(event) => setReviewText(event.target.value)}
                />
                <div className="brief-review-actions">
                  <Button size="sm" type="button" onClick={() => put(reviewText)}>
                    Replace the brief
                  </Button>
                  <Button
                    kind="secondary"
                    size="sm"
                    type="button"
                    onClick={() => put(text.trim() ? `${text.replace(/\s+$/, "")}\n\n${reviewText}` : reviewText)}
                  >
                    Add to the end
                  </Button>
                  <Button kind="quiet" type="button" onClick={() => setReview(null)}>
                    Discard
                  </Button>
                </div>
              </fieldset>
            ) : null}
            <div className="form-foot brief-foot">
              <div>
                <input
                  ref={picker}
                  id="brief-file"
                  className="visually-hidden"
                  type="file"
                  accept=".docx,.pdf,.txt,.md"
                  aria-label="Choose a file to import"
                  onChange={(event) => void onFile(event)}
                />
                <Button
                  kind="secondary"
                  size="md"
                  type="button"
                  disabled={importing}
                  onClick={() => picker.current?.click()}
                >
                  <Icon name="download" size={14} />
                  {importing ? "Reading the file..." : "Import from a file"}
                </Button>
              </div>
              <div className="tpl-foot-right">
                <span className="saved" role="status">
                  {saved ? "Saved" : ""}
                </span>
                <Button size="md" type="button" wide disabled={busy || over > 0 || !dirty} onClick={() => void save()}>
                  {busy ? "Saving..." : "Save brief"}
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}
