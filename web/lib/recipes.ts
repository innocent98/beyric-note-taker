import { ApiError, errorMessage } from "@/lib/api/client";
import type { DocumentRequest, Recipe } from "@/lib/api/types";

// What the recipe screens share (docs/fe-integration-guide-recipes-library.md).

// Refusals whose `message` the server wrote for the person, though their status (422) is otherwise
// developer text: the brief over its cap, every reason a file cannot be read, a busy reader, and a
// full recipe shelf. `recipe_limit` (429) and `plan_inactive` (403) are showable by their status.
export const PERSON_CODES = [
  "brief_too_long",
  "no_text_layer",
  "too_many_pages",
  "unsupported",
  "unreadable",
  "import_busy",
  "too_many_recipes",
  "options_invalid",
] as const;

export function recipeErrorMessage(error: unknown): string {
  if (error instanceof ApiError && (PERSON_CODES as readonly string[]).includes(error.code)) return error.message;
  return errorMessage(error);
}

export type RecipeGroups = { general: Recipe[]; school: Recipe[]; team: Recipe[]; mine: Recipe[] };

// The Make dialog's groups: the two starter packs, then the active team's recipes and the person's own
// (the API already lists them in that order; each group keeps it).
export function groupRecipes(list: Recipe[]): RecipeGroups {
  return {
    general: list.filter((r) => r.source === "starter" && r.pack === "general"),
    school: list.filter((r) => r.source === "starter" && r.pack === "school"),
    team: list.filter((r) => r.source === "team"),
    mine: list.filter((r) => r.source === "personal"),
  };
}

// A starter is named to `POST /api/documents` by its key, a row by its id: exactly one of them.
export function recipeRef(recipe: Recipe): Pick<DocumentRequest, "recipe_key" | "recipe_id"> {
  return recipe.id === null ? { recipe_key: recipe.key } : { recipe_id: recipe.id };
}

// Each choice starts at its default, or empty for a text one.
export function defaultOptions(recipe: Recipe): Record<string, string> {
  return Object.fromEntries(Object.entries(recipe.options).map(([key, option]) => [key, option.default ?? ""]));
}

// Only the choices the person filled go to the model.
export function optionsToSend(values: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(values)) {
    const text = value.trim();
    if (text) out[key] = text;
  }
  return out;
}

// A choice's key from its label: lowercase words joined by `_`, starting with a letter, at most 24
// characters (the API's rule), different from the keys already used.
export function slug(label: string, taken: string[]): string {
  let base = label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!base) base = "choice";
  if (!/^[a-z]/.test(base)) base = `c_${base}`;
  base = base.slice(0, 24).replace(/_+$/, "");
  let key = base;
  for (let n = 2; taken.includes(key); n += 1) {
    const suffix = `_${n}`;
    key = base.slice(0, 24 - suffix.length) + suffix;
  }
  return key;
}
