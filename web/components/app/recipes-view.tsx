"use client";

import { useState, type ReactNode } from "react";
import { BriefEditor } from "@/components/app/brief-editor";
import { RecipeEditor, type EditorMode } from "@/components/app/recipe-editor";
import { GateText } from "@/components/billing/gate-text";
import { Alert, Button, Card, Icon, PageHead } from "@/components/ui";
import type { Brief, Recipe, RecipeList } from "@/lib/api/types";
import { groupRecipes, recipeMatches } from "@/lib/recipes";

type Selected = { kind: "recipe"; value: string } | { kind: "new"; seed?: Recipe; n: number } | null;
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

// The Recipes page. LEAD-DESIGNED, not in the designer's files. It leads with what the person can already use: their
// own recipes and their workspace's, each with a line that says what it is while it is empty. The ready-made recipes
// (the starters, about twenty) sit below under one heading, shut until asked for and searchable, so the page is not a
// wall of names. A ready-made recipe opens as a plain description with one button, "Use this recipe", that makes the
// person's own copy; the editor shows only recipes the person may change. The workspace brief comes after the recipes.
// It is about one workspace (`?team=`, else the tab's): `initial.team` says which and what the person may change.
export function RecipesView({ initial, brief }: { initial: RecipeList; brief: Brief }) {
  const [recipes, setRecipes] = useState(initial.recipes);
  const [selected, setSelected] = useState<Selected>(null);
  const [message, setMessage] = useState<Message>(null);
  const [startersOpen, setStartersOpen] = useState(false);
  const [query, setQuery] = useState("");
  const team = initial.team;
  const groups = groupRecipes(recipes);
  const searching = query.trim() !== "";
  const showStarters = startersOpen || searching;
  const starters = [...groups.general, ...groups.school];

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
  const current = selected?.kind === "recipe" ? (recipes.find((r) => valueOf(r) === selected.value) ?? null) : null;

  const mode: EditorMode | null =
    selected?.kind === "new"
      ? { kind: "new", seed: selected.seed }
      : current && current.can_edit
        ? { kind: "edit", recipe: current }
        : current
          ? { kind: "view", recipe: current }
          : null;
  const copiedFrom =
    current && current.source !== "starter" && current.key ? (recipes.find((r) => r.source === "starter" && r.key === current.key)?.name ?? null) : null;

  const item = (r: Recipe) => (
    <button key={valueOf(r)} type="button" className="pick" aria-current={selected?.kind === "recipe" && selected.value === valueOf(r)} onClick={() => choose(valueOf(r))}>
      <span className="pick-name">{r.name}</span>
      <span className="pick-preview">
        {OUTPUT[r.output_type]}
        {r.scope === "many" ? " · several meetings" : ""}
      </span>
    </button>
  );
  const group = (title: string, list: Recipe[], empty: ReactNode) => (
    <div className="pick-group" key={title}>
      <h3 className="nav-label">{title}</h3>
      {list.length > 0 ? list.map(item) : empty}
    </div>
  );
  const subgroup = (title: string, list: Recipe[]) =>
    list.length === 0 ? null : (
      <div key={title}>
        <h4 className="pick-sub">{title}</h4>
        {list.map(item)}
      </div>
    );

  const editorKey = selected?.kind === "new" ? `new-${selected.n}` : selected ? selected.value : "none";
  const matching = (list: Recipe[]) => list.filter((r) => recipeMatches(r, query));
  const found = matching(starters).length;

  return (
    <div className="stack-md">
      <PageHead title="Recipes" subtitle="A recipe tells Beyric what to write from a meeting. Pick one, or make your own.">
        <Button size="md" type="button" onClick={() => chooseNew()}>
          <Icon name="plus" size={14} />
          New recipe
        </Button>
      </PageHead>
      <div className="split">
        <section className="card split-list" aria-label="Recipes">
          {group(
            "My recipes",
            groups.mine,
            <div className="pick-empty">
              <p className="field-hint">Only you can use these. Nothing here yet: open a ready-made recipe and press Use this recipe, or write your own.</p>
              <Button kind="secondary" size="sm" type="button" onClick={() => setStartersOpen(true)}>
                Browse ready-made recipes
              </Button>
            </div>,
          )}
          {groups.team.length > 0 || !team.is_personal
            ? group(
                "Team recipes",
                groups.team,
                <p className="field-hint pick-empty">
                  Recipes saved for everyone on {team.name}. None yet: {team.can_manage ? "save one and it shows here" : "an owner or admin can save one"}.
                </p>,
              )
            : null}
          <div className="pick-group">
            <h3 className="nav-label">Ready-made recipes</h3>
            <button type="button" className="pick pick-toggle" aria-expanded={showStarters} onClick={() => setStartersOpen(!startersOpen)} disabled={searching}>
              <span className="pick-name">
                {showStarters ? "Hide the ready-made recipes" : `Show the ${starters.length} ready-made recipes`}
              </span>
              <span className="pick-preview">Written by Beyric. Use one and it becomes yours to change.</span>
            </button>
            {showStarters ? (
              <>
                <div className="pick-search">
                  <input
                    type="search"
                    className="input"
                    aria-label="Search the ready-made recipes"
                    placeholder="Search, for example email"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                  />
                </div>
                {subgroup("General", matching(groups.general))}
                {subgroup("School", matching(groups.school))}
                {found === 0 ? <p className="field-hint pick-empty">No ready-made recipe matches “{query.trim()}”.</p> : null}
              </>
            ) : null}
          </div>
        </section>
        <Card>
          {mode === null ? (
            <div className="recipe-intro stack-md" id="recipe-editor">
              {message?.kind === "error" ? (
                <Alert kind="error">
                  <GateText message={message.text} />
                </Alert>
              ) : null}
              <h2 className="brief-title">Pick a recipe</h2>
              <p>A recipe is a set of instructions: what to write (an email, a proposal, a report) and what to ask you first. Open one in the list to see what it makes.</p>
              <p className="field-hint">
                Press <strong>Use this recipe</strong> on a ready-made one to get your own copy, or <strong>New recipe</strong> to write one from scratch. To use a recipe, open a
                meeting and press <strong>Make</strong>.
              </p>
            </div>
          ) : (
            <RecipeEditor
              key={editorKey}
              mode={mode}
              team={team}
              copiedFrom={copiedFrom}
              message={message}
              onMessage={setMessage}
              onCopy={(recipe) => chooseNew(recipe)}
              onSaved={(saved, note) => {
                setRecipes((all) => {
                  const rest = all.filter((r) => r.id !== saved.id);
                  return ordered([...rest, saved]);
                });
                setSelected({ kind: "recipe", value: valueOf(saved) });
                setMessage({ kind: "saved", text: note ?? "Saved" });
              }}
              onArchived={(id) => {
                setRecipes(recipes.filter((r) => r.id !== id));
                setSelected(null);
                setMessage(null);
              }}
              onGone={(id) => {
                setRecipes(recipes.filter((r) => r.id !== id));
                setSelected(null);
              }}
            />
          )}
        </Card>
      </div>
      <BriefEditor initial={brief} />
    </div>
  );
}
