"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Alert, Button, Card, Field, Icon, PageHead, TextareaField } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { Template } from "@/lib/api/types";
import { plural } from "@/lib/format";

const NAME_MAX = 100;
const INSTRUCTIONS_MAX = 4000;

type Selected = number | "new";

// The workspace the templates belong to. Members read and use them; owners and admins write them
// (the server answers 403 `team_forbidden` otherwise, so a member is never shown the controls).
export type TemplatesWorkspace = { name: string; isPersonal: boolean; canEdit: boolean };
const PERSONAL: TemplatesWorkspace = { name: "your personal workspace", isPersonal: true, canEdit: true };

// Templates as the designer draws them: the list on the left, the editor for the chosen template
// on the right, "Add template" at the top. Limits are the server's (name 100, instructions 4000), so
// a 422 should not happen. "Delete template" is the designer's red text button on the left of the
// foot. The design deletes at once; here a saved template asks first (inline, like the Decisions
// log), because a delete cannot be undone. An unsaved new template is only discarded.
//   Per workspace (workspaces design, section 3), LEAD-DESIGNED, the designer's files draw none of it:
//   a member sees the same list and the chosen template's name and instructions read-only, with one
//   notice line naming who can change them, and no Add, Save or Delete; an owner or admin of a team
//   workspace also gets "Copy from my personal workspace" beside "Add template" (an existing
//   secondary button), whose result is one `Alert` line. No new style.
export function TemplatesEditor({
  initial,
  workspace = PERSONAL,
}: {
  initial: Template[];
  workspace?: TemplatesWorkspace;
}) {
  const canEdit = workspace.canEdit;
  const [copyNote, setCopyNote] = useState<string | null>(null);
  const [copying, setCopying] = useState(false);
  const [templates, setTemplates] = useState(initial);
  const [selected, setSelected] = useState<Selected>(initial[0]?.id ?? "new");
  const [name, setName] = useState(initial[0]?.name ?? "");
  const [instructions, setInstructions] = useState(initial[0]?.instructions ?? "");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  function choose(next: Selected) {
    const template = templates.find((t) => t.id === next);
    setSelected(next);
    setName(template?.name ?? "");
    setInstructions(template?.instructions ?? "");
    setError(null);
    setSaved(false);
    setConfirming(false);
  }

  // After a delete the designer selects the first template of the list.
  function chooseFirstOf(rest: Template[]) {
    setTemplates(rest);
    setSelected(rest[0]?.id ?? "new");
    setName(rest[0]?.name ?? "");
    setInstructions(rest[0]?.instructions ?? "");
    setSaved(false);
    setConfirming(false);
  }

  function askToDelete() {
    if (selected === "new") {
      setError(null);
      chooseFirstOf(templates); // nothing was saved: drop the draft
    } else {
      setConfirming(true);
    }
  }

  async function remove() {
    if (deleting || selected === "new") return;
    const id = selected;
    setDeleting(true);
    setError(null);
    try {
      await api.templates.remove(id);
      chooseFirstOf(templates.filter((t) => t.id !== id));
    } catch (caught) {
      setConfirming(false);
      if (caught instanceof ApiError && caught.status === 404) {
        // Gone already (another tab, a double press): the list should not show it any more.
        chooseFirstOf(templates.filter((t) => t.id !== id));
        setError("That template was already deleted.");
      } else {
        setError(errorMessage(caught)); // 409 `last_template` has its own message for the person
      }
    }
    setDeleting(false);
  }

  async function copyFromPersonal() {
    if (copying) return;
    setCopying(true);
    setError(null);
    setCopyNote(null);
    try {
      const result = await api.templates.copyFromPersonal();
      setTemplates(result.templates);
      if (selected === "new" || !result.templates.some((t) => t.id === selected)) {
        const first = result.templates[0];
        setSelected(first?.id ?? "new");
        setName(first?.name ?? "");
        setInstructions(first?.instructions ?? "");
      }
      setCopyNote(
        result.copied === 0
          ? "Nothing to copy: every personal template is already here."
          : `Copied ${plural(result.copied, "template")} from your personal workspace.` +
              (result.skipped > 0 ? ` ${result.skipped} already here, left as they were.` : ""),
      );
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setCopying(false);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || !canEdit) return;
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
    <div className="stack-md">
      <PageHead title="Templates" subtitle="Instructions tell the notes writer what to focus on.">
        {canEdit && !workspace.isPersonal ? (
          <Button kind="secondary" size="md" type="button" disabled={copying} onClick={() => void copyFromPersonal()}>
            {copying ? "Copying..." : "Copy from my personal workspace"}
          </Button>
        ) : null}
        {canEdit ? (
          <Button size="md" type="button" onClick={() => choose("new")}>
            <Icon name="plus" size={14} />
            Add template
          </Button>
        ) : null}
      </PageHead>
      {!canEdit ? (
        <Alert kind="info">{`Only owners and admins of ${workspace.name} can change its templates. You can use them for your meetings.`}</Alert>
      ) : null}
      {copyNote ? <Alert kind="info">{copyNote}</Alert> : null}
      <div className="split">
        <section className="card split-list" aria-label={workspace.isPersonal ? "Your templates" : `Templates of ${workspace.name}`}>
          {templates.map((t) => (
            <button
              key={t.id}
              type="button"
              className="pick"
              aria-current={selected === t.id}
              onClick={() => choose(t.id)}
            >
              <span className="pick-name">{t.name || "Untitled"}</span>
              <span className="pick-preview">{t.instructions || "No instructions yet"}</span>
            </button>
          ))}
          {selected === "new" ? (
            <button type="button" className="pick" aria-current="true">
              <span className="pick-name">{name.trim() || "Untitled"}</span>
              <span className="pick-preview">{instructions.trim() || "No instructions yet"}</span>
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
              readOnly={!canEdit}
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
              readOnly={!canEdit}
              placeholder="What should the notes focus on?"
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
            />
            {canEdit ? (
            <div className="form-foot tpl-foot">
              <div className="tpl-foot-left">
                {confirming ? (
                  <div className="tpl-confirm" role="group" aria-label="Delete this template?">
                    <span>Delete this template? Meetings already written with it keep their notes.</span>
                    <Button kind="secondary" size="sm" type="button" disabled={deleting} onClick={() => void remove()}>
                      {deleting ? "Deleting..." : "Delete"}
                    </Button>
                    <Button kind="quiet" type="button" disabled={deleting} onClick={() => setConfirming(false)}>
                      Cancel
                    </Button>
                  </div>
                ) : (
                  <button type="button" className="tpl-delete" disabled={pending} onClick={askToDelete}>
                    Delete template
                  </button>
                )}
              </div>
              <div className="tpl-foot-right">
                <span className="saved" role="status">
                  {saved ? "Saved" : ""}
                </span>
                <Button size="md" wide disabled={pending || deleting}>
                  {pending ? "Saving..." : "Save"}
                </Button>
              </div>
            </div>
            ) : null}
          </form>
        </Card>
      </div>
    </div>
  );
}
