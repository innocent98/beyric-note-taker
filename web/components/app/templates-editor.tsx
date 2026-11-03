"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Alert, Button, Card, Field, Icon, PageHead, TextareaField } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { Template } from "@/lib/api/types";

const NAME_MAX = 100;
const INSTRUCTIONS_MAX = 4000;

type Selected = number | "new";

// Templates as the designer draws them: the list on the left, the editor for the chosen template
// on the right, "Add template" at the top. Limits are the server's (name 100, instructions 4000), so
// a 422 should not happen. There is no delete: the API has none yet.
export function TemplatesEditor({ initial }: { initial: Template[] }) {
  const [templates, setTemplates] = useState(initial);
  const [selected, setSelected] = useState<Selected>(initial[0]?.id ?? "new");
  const [name, setName] = useState(initial[0]?.name ?? "");
  const [instructions, setInstructions] = useState(initial[0]?.instructions ?? "");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  function choose(next: Selected) {
    const template = templates.find((t) => t.id === next);
    setSelected(next);
    setName(template?.name ?? "");
    setInstructions(template?.instructions ?? "");
    setError(null);
    setSaved(false);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    setError(null);
    setSaved(false);
    try {
      const body = { name: name.trim(), instructions: instructions.trim() };
      const result = selected === "new" ? await api.templates.create(body) : await api.templates.update(selected, body);
      setTemplates((current) =>
        [...current.filter((t) => t.id !== result.id), result].sort((a, b) => a.name.localeCompare(b.name)),
      );
      setSelected(result.id);
      setName(result.name);
      setInstructions(result.instructions);
      setSaved(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setSaved(false), 3000);
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setPending(false);
  }

  return (
    <div className="stack">
      <PageHead title="Templates" subtitle="Instructions tell the notes writer what to focus on.">
        <Button type="button" onClick={() => choose("new")}>
          <Icon name="plus" size={14} />
          Add template
        </Button>
      </PageHead>
      <div className="split">
        <section className="card split-list" aria-label="Your templates">
          {templates.map((t) => (
            <button
              key={t.id}
              type="button"
              className="pick"
              aria-current={selected === t.id}
              onClick={() => choose(t.id)}
            >
              <span className="pick-name">{t.name}</span>
              <span className="pick-preview">{t.instructions}</span>
            </button>
          ))}
          {selected === "new" ? (
            <button type="button" className="pick" aria-current="true">
              <span className="pick-name">{name.trim() || "New template"}</span>
              <span className="pick-preview">Not saved yet</span>
            </button>
          ) : null}
        </section>
        <Card>
          <form className="split-form" onSubmit={save}>
            <h2 className="visually-hidden">{selected === "new" ? "Add a template" : `Edit ${name}`}</h2>
            {error ? <Alert kind="error">{error}</Alert> : null}
            <Field
              id="template-name"
              label="Name"
              required
              maxLength={NAME_MAX}
              placeholder="e.g. Weekly sync"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <TextareaField
              id="template-instructions"
              label="Instructions"
              rows={7}
              required
              maxLength={INSTRUCTIONS_MAX}
              placeholder="What should the notes focus on?"
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
            />
            <div className="form-foot">
              <span className="saved" role="status">
                {saved ? "Saved" : ""}
              </span>
              <Button disabled={pending}>
                {pending ? "Saving..." : selected === "new" ? "Add template" : "Save"}
              </Button>
            </div>
          </form>
        </Card>
      </div>
    </div>
  );
}
