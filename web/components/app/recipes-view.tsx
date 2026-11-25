"use client";

import { useState } from "react";
import { BriefEditor } from "@/components/app/brief-editor";
import { RecipeEditor, type EditorMode } from "@/components/app/recipe-editor";
import { Button, Card, Icon, PageHead } from "@/components/ui";
import type { Brief, Recipe, RecipeList } from "@/lib/api/types";
import { groupRecipes } from "@/lib/recipes";

type Selected = { kind: "recipe"; value: string } | { kind: "new"; seed?: Recipe; n: number };
type Message = { kind: "saved" | "error"; text: string } | null;

// Below the two-column layout the editor sits under the list: bring it into view when something is chosen.
function showEditor() {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function" || !window.matchMedia("(max-width: 860px)").matches) return;
  window.requestAnimationFrame(() => document.getElementById("recipe-editor")?.scrollIntoView?.({ behavior: "smooth", block: "start" }));
}

const valueOf = (r: Recipe) => (r.id === null ? (r.key ?? "") : String(r.id));
const OUTPUT: Record<Recipe["output_type"], string> = { document: "Document", email: "Email", table: "Table", board: "Board" };

// The list as the API gives it: starters, then the team's, then the person's own.
function ordered(all: Recipe[]): Recipe[] {
  const g = groupRecipes(all);
  return [...g.general, ...g.school, ...g.team, ...g.mine];
}

// The Recipes page: the team's brief, then the library: the starters (General and School), the team's recipes
// and the person's own, with the editor beside the list (the Templates screen's shape). LEAD-DESIGNED, not in the
// designer's files. It is about one team (`?team=`, else the tab's): `initial.team` says which and what the person
// may change.
export function RecipesView({ initial, brief }: { initial: RecipeList; brief: Brief }) {
  const [recipes, setRecipes] = useState(initial.recipes);
  const [selected, setSelected] = useState<Selected>({ kind: "recipe", value: valueOf(initial.recipes[0]) });
  const [message, setMessage] = useState<Message>(null);
  const team = initial.team;
  const groups = groupRecipes(recipes);

  const choose = (value: string) => {
    setSelected({ kind: "recipe", value });
    setMessage(null);
    showEditor();
  };
  const chooseNew = (seed?: Recipe) => {
    setSelected({ kind: "new", seed, n: Date.now() });
    setMessage(null);
    showEditor();
  };
  const first = (list: Recipe[]) => valueOf(ordered(list)[0]);
  const current = selected.kind === "recipe" ? (recipes.find((r) => valueOf(r) === selected.value) ?? null) : null;

  const mode: EditorMode =
    selected.kind === "new"
      ? { kind: "new", seed: selected.seed }
      : current && current.can_edit
        ? { kind: "edit", recipe: current }
        : { kind: "view", recipe: current ?? recipes[0] };
  const copiedFrom =
    current && current.source !== "starter" && current.key ? (recipes.find((r) => r.source === "starter" && r.key === current.key)?.name ?? null) : null;

  const item = (r: Recipe) => (
    <button key={valueOf(r)} type="button" className="pick" aria-current={selected.kind === "recipe" && selected.value === valueOf(r)} onClick={() => choose(valueOf(r))}>
      <span className="pick-name">{r.name}</span>
      <span className="pick-preview">
        {OUTPUT[r.output_type]}
        {r.scope === "many" ? " · several meetings" : ""}
      </span>
    </button>
  );
  const group = (title: string, list: Recipe[]) =>
    list.length === 0 ? null : (
      <div className="pick-group" key={title}>
        <h3 className="nav-label">{title}</h3>
        {list.map(item)}
      </div>
    );

  // The groups with nothing in them are left out, so the person cannot tell where theirs will appear: say so.
  const hints = [
    groups.team.length === 0 && !team.is_personal ? `Team recipes appear here once an owner or admin of ${team.name} saves one for everyone.` : null,
    groups.mine.length === 0 ? 'My recipes appear here, under the starters, once you copy a starter ("Make a copy to change") or press New recipe.' : null,
  ].filter((text): text is string => text !== null);

  return (
    <div className="stack-md">
      <PageHead title="Recipes" subtitle="What Beyric can make from a meeting. Copy one and change it, or write your own.">
        <Button size="md" type="button" onClick={() => chooseNew()}>
          <Icon name="plus" size={14} />
          New recipe
        </Button>
      </PageHead>
      <BriefEditor initial={brief} />
      <div className="split">
        <section className="card split-list" aria-label="Recipes">
          {group("General", groups.general)}
          {group("School", groups.school)}
          {group("Team recipes", groups.team)}
          {group("My recipes", groups.mine)}
          {hints.map((text) => (
            <p className="field-hint pick-hint" key={text}>
              {text}
            </p>
          ))}
        </section>
        <Card>
          <RecipeEditor
            key={selected.kind === "new" ? `new-${selected.n}` : selected.value}
            mode={mode}
            team={team}
            copiedFrom={copiedFrom}
            message={message}
            onMessage={setMessage}
            onCopy={(recipe) => chooseNew(recipe)}
            onSaved={(saved) => {
              setRecipes((all) => {
                const rest = all.filter((r) => r.id !== saved.id);
                return ordered([...rest, saved]);
              });
              setSelected({ kind: "recipe", value: valueOf(saved) });
              setMessage({ kind: "saved", text: "Saved" });
            }}
            onArchived={(id) => {
              const rest = recipes.filter((r) => r.id !== id);
              setRecipes(rest);
              setSelected({ kind: "recipe", value: first(rest) });
              setMessage(null);
            }}
            onGone={(id) => {
              const rest = recipes.filter((r) => r.id !== id);
              setRecipes(rest);
              setSelected({ kind: "recipe", value: first(rest) });
            }}
          />
        </Card>
      </div>
    </div>
  );
}
