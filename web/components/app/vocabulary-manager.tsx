"use client";

import { useState, type FormEvent } from "react";
import { Alert, Button, Card, Field, IconButton, PageHead } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { Term } from "@/lib/api/types";
import { plural } from "@/lib/format";

// The workspace the terms belong to: members read the list (it is used for every meeting of the
// workspace), owners and admins write it.
export type VocabularyWorkspace = { name: string; isPersonal: boolean; canEdit: boolean };
const PERSONAL: VocabularyWorkspace = { name: "your personal workspace", isPersonal: true, canEdit: true };

// Vocabulary: add a term, delete a term. After an add the list is read again so the order is the
// server's (alphabetical). Deleting a term that is already gone is a 404 from the API: the term
// is gone either way, so it is removed from the list as a success.
//   Per workspace (workspaces design, section 3), LEAD-DESIGNED: a member sees the list and one notice
//   line naming who can change it, without the add form or the remove buttons; an owner or admin of a
//   team workspace also gets "Copy from my personal workspace" (an existing secondary button) and its
//   result in one `Alert` line. No new style.
export function VocabularyManager({
  initial,
  workspace = PERSONAL,
}: {
  initial: Term[];
  workspace?: VocabularyWorkspace;
}) {
  const canEdit = workspace.canEdit;
  const [copyNote, setCopyNote] = useState<string | null>(null);
  const [copying, setCopying] = useState(false);
  const [terms, setTerms] = useState(initial);
  const [term, setTerm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<number | null>(null);

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (adding) return;
    setAdding(true);
    setError(null);
    try {
      await api.vocabulary.add(term.trim());
      setTerm("");
      setTerms(await api.vocabulary.list());
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setAdding(false);
  }

  async function copyFromPersonal() {
    if (copying) return;
    setCopying(true);
    setError(null);
    setCopyNote(null);
    try {
      const result = await api.vocabulary.copyFromPersonal();
      setTerms(result.terms);
      setCopyNote(
        result.copied === 0
          ? "Nothing to copy: every personal term is already here."
          : `Copied ${plural(result.copied, "term")} from your personal workspace.` +
              (result.skipped > 0 ? ` ${result.skipped} already here, left as they were.` : ""),
      );
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setCopying(false);
  }

  async function remove(id: number) {
    if (removing !== null) return;
    setRemoving(id);
    setError(null);
    try {
      await api.vocabulary.remove(id);
      setTerms((current) => current.filter((t) => t.id !== id));
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        setTerms((current) => current.filter((t) => t.id !== id));
      } else {
        setError(errorMessage(caught));
      }
    }
    setRemoving(null);
  }

  return (
    <div className="stack-md measure">
      <PageHead title="Vocabulary" subtitle="Names and terms to spell correctly in transcripts and notes." />
      {error ? <Alert kind="error">{error}</Alert> : null}
      {!canEdit ? (
        <Alert kind="info">{`Only owners and admins of ${workspace.name} can change its vocabulary. It is used for every meeting recorded there.`}</Alert>
      ) : null}
      {canEdit && !workspace.isPersonal ? (
        <div>
          <Button kind="secondary" type="button" disabled={copying} onClick={() => void copyFromPersonal()}>
            {copying ? "Copying..." : "Copy from my personal workspace"}
          </Button>
        </div>
      ) : null}
      {copyNote ? <Alert kind="info">{copyNote}</Alert> : null}
      <Card flush>
        {canEdit ? (
        <form className="add-term" onSubmit={add}>
          <Field
            id="term"
            label="New term"
            hideLabel
            required
            maxLength={100}
            autoComplete="off"
            placeholder="Add a name, product or acronym"
            value={term}
            onChange={(event) => setTerm(event.target.value)}
          />
          <Button disabled={adding}>{adding ? "Adding..." : "Add term"}</Button>
        </form>
        ) : null}
        <div className="count-line">{plural(terms.length, "term")}</div>
        <ul className="list">
          {terms.map((t) => (
            <li className="term-row" key={t.id}>
              <span className="term-name">{t.term}</span>
              {canEdit ? (
                <IconButton
                  icon="trash"
                  tone="danger"
                  label={`Remove ${t.term}`}
                  tooltip="Remove"
                  disabled={removing !== null}
                  onClick={() => void remove(t.id)}
                />
              ) : null}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
