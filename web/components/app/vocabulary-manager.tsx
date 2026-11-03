"use client";

import { useState, type FormEvent } from "react";
import { Alert, Button, Card, EmptyState, Field, IconButton, PageHead } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { Term } from "@/lib/api/types";
import { plural } from "@/lib/format";

// Vocabulary: add a term, delete a term. After an add the list is read again so the order is the
// server's (alphabetical). Deleting a term that is already gone is a 404 from the API: the term
// is gone either way, so it is removed from the list as a success.
export function VocabularyManager({ initial }: { initial: Term[] }) {
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
      <Card flush>
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
        {terms.length > 0 ? (
          <>
            <div className="count-line">{plural(terms.length, "term")}</div>
            <ul className="list">
              {terms.map((t) => (
                <li className="term-row" key={t.id}>
                  <span className="term-name">{t.term}</span>
                  <IconButton
                    icon="trash"
                    tone="danger"
                    label={`Delete ${t.term}`}
                    disabled={removing !== null}
                    onClick={() => void remove(t.id)}
                  />
                </li>
              ))}
            </ul>
          </>
        ) : (
          <EmptyState title="No terms yet.">Add names and terms above and they will be spelled your way.</EmptyState>
        )}
      </Card>
    </div>
  );
}
