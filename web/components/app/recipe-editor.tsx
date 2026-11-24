"use client";

import { useState, type FormEvent } from "react";
import { GateText } from "@/components/billing/gate-text";
import { Alert, Button, Field, Icon, SelectField, TextareaField } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError } from "@/lib/api/client";
import type { Recipe, RecipeOption, RecipeTeam } from "@/lib/api/types";
import { recipeErrorMessage, slug } from "@/lib/recipes";

export const NAME_MAX = 100;
export const INSTRUCTION_MAX = 4000;
export const MAX_CHOICES = 6;
const GONE = "That recipe is gone. It may have been archived in another tab.";

export type EditorMode =
  | { kind: "view"; recipe: Recipe } // a starter, or a team recipe read by a member
  | { kind: "edit"; recipe: Recipe }
  | { kind: "new"; seed?: Recipe }; // blank, or a copy of a starter (`seed`)

type Row = { key: string | null; label: string; kind: "text" | "choice"; words: string; keep: string | null };

const toRows = (options: Record<string, RecipeOption>): Row[] =>
  Object.entries(options).map(([key, o]) => ({ key, label: o.label, kind: o.kind, words: o.choices.join(", "), keep: o.default ?? null }));

const wordsOf = (row: Row) =>
  row.words
    .split(",")
    .map((w) => w.trim())
    .filter(Boolean);

// The choices as the API takes them: a kept key stays (a recipe's older drafts and a copy's source name them), a new
// row gets one made from its label.
function toOptions(rows: Row[]): Record<string, RecipeOption> {
  const out: Record<string, RecipeOption> = {};
  const taken = rows.map((r) => r.key).filter((k): k is string => k !== null);
  for (const row of rows) {
    const key = row.key ?? slug(row.label, [...taken, ...Object.keys(out)]);
    const words = row.kind === "choice" ? wordsOf(row) : [];
    out[key] = {
      label: row.label.trim(),
      kind: row.kind,
      choices: words,
      default: row.kind === "choice" && row.keep && words.includes(row.keep) ? row.keep : null,
    };
  }
  return out;
}

type Fields = { name: string; instruction: string; output_type: Recipe["output_type"]; scope: Recipe["scope"]; uses_brief: boolean };

// The recipe editor beside the list. LEAD-DESIGNED (the designer's files draw none of recipes), laid out like the
// Templates editor. A starter, and a team recipe read by a member, are shown whole and read-only with a way to copy
// them. `new` is blank or a copy of a starter (`seed`); an owner or admin may make it the team's.
export function RecipeEditor({
  mode,
  team,
  copiedFrom,
  message,
  onSaved,
  onArchived,
  onGone,
  onCopy,
  onMessage,
}: {
  mode: EditorMode;
  team: RecipeTeam;
  copiedFrom?: string | null;
  message: { kind: "saved" | "error"; text: string } | null;
  onSaved: (recipe: Recipe) => void;
  onArchived: (id: number) => void;
  onGone: (id: number) => void;
  onCopy: (recipe: Recipe) => void;
  onMessage: (message: { kind: "saved" | "error"; text: string } | null) => void;
}) {
  const source = mode.kind === "new" ? mode.seed : mode.recipe;
  const readOnly = mode.kind === "view";
  const [fields, setFields] = useState<Fields>({
    name: mode.kind === "new" && source ? `${source.name} (copy)` : (source?.name ?? ""),
    instruction: source?.instruction ?? "",
    output_type: source?.output_type ?? "document",
    scope: source?.scope ?? "one",
    uses_brief: source?.uses_brief ?? true,
  });
  const [rows, setRows] = useState<Row[]>(() => toRows(source?.options ?? {}));
  const [visibility, setVisibility] = useState<"personal" | "team">("personal");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const set = <K extends keyof Fields>(key: K, value: Fields[K]) => setFields((f) => ({ ...f, [key]: value }));
  const setRow = (index: number, change: Partial<Row>) => setRows((all) => all.map((r, i) => (i === index ? { ...r, ...change } : r)));

  const unnamed = rows.some((r) => !r.label.trim());
  const wordless = rows.some((r) => r.kind === "choice" && wordsOf(r).length === 0);
  const complete = fields.name.trim() !== "" && fields.instruction.trim() !== "" && !unnamed && !wordless;
  const payload = () => ({
    name: fields.name.trim(),
    instruction: fields.instruction.trim(),
    output_type: fields.output_type,
    options: toOptions(rows),
    scope: fields.scope,
    uses_brief: fields.uses_brief,
  });
  // Is there anything to save? Compared with what the recipe is now, field by field.
  const shape = (f: Fields, options: Record<string, RecipeOption>) =>
    JSON.stringify([f.name.trim(), f.instruction.trim(), f.output_type, f.scope, f.uses_brief, options]);
  const dirty =
    mode.kind === "new" ||
    (mode.kind === "edit" && shape(fields, toOptions(rows)) !== shape(mode.recipe, mode.recipe.options));

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy || readOnly || !complete) return;
    setBusy(true);
    onMessage(null);
    try {
      if (mode.kind === "new") {
        const made = await api.recipes.create({
          ...(mode.seed ? { key: mode.seed.key } : {}),
          ...payload(),
          visibility: team.can_manage ? visibility : "personal",
        });
        onSaved(made);
      } else if (mode.kind === "edit") {
        onSaved(await api.recipes.update(mode.recipe.id as number, payload()));
      }
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404 && mode.kind === "edit") {
        onGone(mode.recipe.id as number);
        onMessage({ kind: "error", text: GONE });
      } else {
        onMessage({ kind: "error", text: recipeErrorMessage(caught) });
      }
    }
    setBusy(false);
  }

  async function archive() {
    if (busy || mode.kind !== "edit") return;
    const id = mode.recipe.id as number;
    setBusy(true);
    try {
      await api.recipes.archive(id);
      onArchived(id);
    } catch (caught) {
      setConfirming(false);
      if (caught instanceof ApiError && caught.status === 404) {
        onGone(id);
        onMessage({ kind: "error", text: GONE });
      } else {
        onMessage({ kind: "error", text: recipeErrorMessage(caught) });
      }
    }
    setBusy(false);
  }

  const note =
    mode.kind !== "view"
      ? null
      : mode.recipe.source === "starter"
        ? "A starter cannot be changed. Make a copy and the copy is yours to change."
        : "Only an owner or admin of the team can change it. Make a copy to change it for yourself.";

  return (
    <form id="recipe-editor" className="split-form" aria-label="Recipe" onSubmit={(event) => void save(event)}>
      {message?.kind === "error" ? (
        <Alert kind="error">
          <GateText message={message.text} />
        </Alert>
      ) : null}
      {note ? <p className="field-hint">{note}</p> : null}
      {copiedFrom ? <p className="field-hint">Copied from {copiedFrom}.</p> : null}
      <Field
        id="recipe-name"
        label="Name"
        required
        maxLength={NAME_MAX}
        readOnly={readOnly}
        value={fields.name}
        onChange={(event) => set("name", event.target.value)}
      />
      <TextareaField
        id="recipe-instruction"
        label="Instruction"
        hint="What to make, and how. Beyric uses only the meeting and the brief, and leaves a blank where a fact is missing."
        rows={9}
        required
        maxLength={INSTRUCTION_MAX}
        readOnly={readOnly}
        value={fields.instruction}
        onChange={(event) => set("instruction", event.target.value)}
      />
      <div className="recipe-pair">
        <SelectField
          id="recipe-output"
          label="Output"
          disabled={readOnly}
          value={fields.output_type}
          options={[
            ["document", "Document"],
            ["email", "Email"],
            ["table", "Table"],
            ["board", "Board"],
          ]}
          onChange={(event) => set("output_type", event.target.value as Recipe["output_type"])}
        />
        <SelectField
          id="recipe-scope"
          label="Made from"
          disabled={readOnly}
          value={fields.scope}
          options={[
            ["one", "One meeting"],
            ["many", "Several meetings"],
          ]}
          onChange={(event) => set("scope", event.target.value as Recipe["scope"])}
        />
      </div>
      <label className="sh-check">
        <input
          type="checkbox"
          className="sh-box"
          disabled={readOnly}
          checked={fields.uses_brief}
          onChange={(event) => set("uses_brief", event.target.checked)}
        />
        Use the team brief
      </label>
      <div className="field">
        <span className="field-label">Choices</span>
        <span className="field-hint">What the person is asked when making a draft, such as a tone or a client name.</span>
        {rows.map((row, index) => (
          <div className="recipe-choice" key={index}>
            <Field
              id={`choice-${index}-name`}
              label={`Choice ${index + 1} name`}
              hideLabel
              placeholder="e.g. Tone"
              maxLength={60}
              readOnly={readOnly}
              value={row.label}
              onChange={(event) => setRow(index, { label: event.target.value })}
            />
            <SelectField
              id={`choice-${index}-kind`}
              label={`Choice ${index + 1} kind`}
              hideLabel
              disabled={readOnly}
              value={row.kind}
              options={[
                ["text", "Text"],
                ["choice", "Word choice"],
              ]}
              onChange={(event) => setRow(index, { kind: event.target.value as Row["kind"] })}
            />
            {row.kind === "choice" ? (
              <Field
                id={`choice-${index}-words`}
                label={`Choice ${index + 1} words`}
                hideLabel
                placeholder="Friendly, Formal"
                readOnly={readOnly}
                value={row.words}
                onChange={(event) => setRow(index, { words: event.target.value })}
              />
            ) : null}
            {readOnly ? null : (
              <button
                type="button"
                className="recipe-choice-remove"
                aria-label={`Remove choice ${index + 1}`}
                onClick={() => setRows((all) => all.filter((_, i) => i !== index))}
              >
                <Icon name="close" size={14} />
              </button>
            )}
          </div>
        ))}
        {wordless ? <span className="field-hint">Give each word choice at least one word, separated by commas.</span> : null}
        {unnamed ? <span className="field-hint">Name every choice.</span> : null}
        {readOnly ? null : (
          <div>
            <Button
              kind="secondary"
              size="sm"
              type="button"
              disabled={rows.length >= MAX_CHOICES}
              onClick={() => setRows((all) => [...all, { key: null, label: "", kind: "text", words: "", keep: null }])}
            >
              <Icon name="plus" size={14} />
              Add a choice
            </Button>
          </div>
        )}
      </div>
      {mode.kind === "new" && team.can_manage ? (
        <fieldset className="field recipe-who">
          <legend className="field-label">Who can use it</legend>
          <div className="segmented" role="radiogroup" aria-label="Who can use it">
            {(
              [
                ["personal", "Only me"],
                ["team", `Everyone on ${team.name}`],
              ] as const
            ).map(([value, label]) => (
              <label className="segmented-opt" key={value}>
                <input type="radio" name="recipe-visibility" checked={visibility === value} onChange={() => setVisibility(value)} />
                <span>{label}</span>
              </label>
            ))}
          </div>
          {visibility === "team" ? (
            <span className="field-hint">The team can use it and read its instruction. Only owners and admins can change it.</span>
          ) : null}
        </fieldset>
      ) : null}
      {readOnly ? (
        <div className="form-foot">
          <Button type="button" size="md" onClick={() => onCopy(mode.recipe)}>
            Make a copy to change
          </Button>
        </div>
      ) : (
        <div className="form-foot tpl-foot">
          <div className="tpl-foot-left">
            {mode.kind !== "edit" ? null : confirming ? (
              <div className="tpl-confirm" role="group" aria-label="Archive this recipe?">
                <span>Archive this recipe? Drafts already made from it are kept.</span>
                <Button kind="secondary" size="sm" type="button" disabled={busy} onClick={() => void archive()}>
                  Archive
                </Button>
                <Button kind="quiet" type="button" disabled={busy} onClick={() => setConfirming(false)}>
                  Cancel
                </Button>
              </div>
            ) : (
              <button type="button" className="tpl-delete" disabled={busy} onClick={() => setConfirming(true)}>
                Archive recipe
              </button>
            )}
          </div>
          <div className="tpl-foot-right">
            <span className="saved" role="status">
              {message?.kind === "saved" ? "Saved" : ""}
            </span>
            <Button size="md" wide disabled={busy || !complete || !dirty}>
              {busy ? "Saving..." : "Save"}
            </Button>
          </div>
        </div>
      )}
    </form>
  );
}
