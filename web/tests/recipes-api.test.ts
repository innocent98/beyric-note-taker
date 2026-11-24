import { describe, expect, it, vi } from "vitest";
import { createApi, isTeamScoped, TEAM_HEADER } from "@/lib/api/client";
import {
  PERSON_CODES,
  defaultOptions,
  groupRecipes,
  optionsToSend,
  recipeErrorMessage,
  recipeRef,
  slug,
} from "@/lib/recipes";
import { ApiError } from "@/lib/api/client";
import type { Recipe } from "@/lib/api/types";
import { recipesLibraryBody } from "./helpers";

// The recipe library, the team brief and the brief import (docs/fe-integration-guide-recipes-library.md):
// the team header on their routes, the multipart upload, and the pure helpers the screens share.
// Bodies are the ones captured from the running API (docs/fe-captures/recipes-library/).

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const session = { csrf_token: "tok", authenticated: true };

function setup(teamId?: () => number | null) {
  const calls: { url: string; init: RequestInit }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    calls.push({ url: String(input), init });
    return json(200, session);
  });
  return { api: createApi({ fetch: fetchMock as unknown as typeof fetch, teamId }), calls };
}
const headers = (call: { init: RequestInit }) => call.init.headers as Record<string, string>;
const last = (calls: { url: string; init: RequestInit }[]) => calls[calls.length - 1];

describe("which recipe calls carry the team", () => {
  it.each(["/api/recipes", "/api/recipes/4", "/api/briefs", "/api/briefs/import"])("%s is team-scoped", (path) => {
    expect(isTeamScoped(path)).toBe(true);
    expect(isTeamScoped(path, "POST")).toBe(true);
  });
  it("only the request for a draft names a team, among the draft routes", () => {
    expect(isTeamScoped("/api/documents", "POST")).toBe(true);
    expect(isTeamScoped("/api/documents", "GET")).toBe(false);
    expect(isTeamScoped("/api/documents/3", "GET")).toBe(false);
    expect(isTeamScoped("/api/documents/3", "PATCH")).toBe(false);
  });
});

describe("the calls", () => {
  it("lists recipes with the tab's team, or the call's own", async () => {
    const { api, calls } = setup(() => 5);
    await api.recipes.list();
    await api.recipes.list(8);
    expect(calls.map((c) => c.url)).toEqual(["/api/recipes", "/api/recipes"]);
    expect(calls.map((c) => headers(c)[TEAM_HEADER])).toEqual(["5", "8"]);
  });

  it("creates, edits and archives with the CSRF token and the team", async () => {
    const { api, calls } = setup(() => 5);
    await api.recipes.create({ name: "A", instruction: "B", visibility: "team" });
    expect(last(calls).init.method).toBe("POST");
    expect(JSON.parse(String(last(calls).init.body))).toEqual({ name: "A", instruction: "B", visibility: "team" });
    expect(headers(last(calls))["X-CSRF-Token"]).toBe("tok");
    expect(headers(last(calls))[TEAM_HEADER]).toBe("5");
    await api.recipes.update(3, { name: "C" });
    expect([last(calls).url, last(calls).init.method]).toEqual(["/api/recipes/3", "PUT"]);
    await api.recipes.archive(3);
    expect([last(calls).url, last(calls).init.method]).toEqual(["/api/recipes/3", "DELETE"]);
  });

  it("reads and saves the brief of the named team", async () => {
    const { api, calls } = setup(() => 5);
    await api.brief.get(9);
    expect(headers(last(calls))[TEAM_HEADER]).toBe("9");
    await api.brief.save("Prices.", 9);
    expect(last(calls).init.method).toBe("PUT");
    expect(JSON.parse(String(last(calls).init.body))).toEqual({ text: "Prices." });
    expect(headers(last(calls))[TEAM_HEADER]).toBe("9");
  });

  it("uploads a brief file as multipart: the browser sets the boundary, the CSRF token goes along", async () => {
    const { api, calls } = setup(() => 5);
    const file = new File(["Terms"], "terms.txt", { type: "text/plain" });
    await api.brief.importFile(file);
    const call = last(calls);
    expect([call.url, call.init.method]).toEqual(["/api/briefs/import", "POST"]);
    expect(call.init.body).toBeInstanceOf(FormData);
    expect((call.init.body as FormData).get("file")).toBeInstanceOf(File);
    expect(((call.init.body as FormData).get("file") as File).name).toBe("terms.txt");
    expect(headers(call)["Content-Type"]).toBeUndefined();
    expect(headers(call)["X-CSRF-Token"]).toBe("tok");
    expect(headers(call)[TEAM_HEADER]).toBe("5");
  });

  it("asks for a draft with the team the dialog chose", async () => {
    const { api, calls } = setup(() => 5);
    await api.documents.create({ recipe_key: "follow_up_email", meeting_ids: [1], options: {}, use_brief: true }, 8);
    expect(last(calls).url).toBe("/api/documents");
    expect(headers(last(calls))[TEAM_HEADER]).toBe("8");
  });
});

describe("the helpers", () => {
  const list = recipesLibraryBody<{ recipes: Recipe[] }>("11-list-starters.json").recipes;
  const member = recipesLibraryBody<{ recipes: Recipe[] }>("17-list-as-member.json").recipes;

  it("groups the list as the dialog draws it: General, School, the team's, then the person's own", () => {
    const groups = groupRecipes(list);
    expect(groups.general.map((r) => r.key)).toEqual([
      "follow_up_email",
      "proposal",
      "requirements",
      "marketing_post",
      "decision_memo",
      "sop",
      "action_tracker",
      "board",
      "status_report",
      "client_briefing",
    ]);
    expect(groups.school).toHaveLength(8);
    expect(groups.team).toEqual([]);
    expect(groups.mine).toEqual([]);
    const withTeam = groupRecipes(member);
    expect(withTeam.team.map((r) => r.name)).toEqual(["House proposal", "Admin recipe"]);
    expect(withTeam.team.every((r) => !r.can_edit)).toBe(true);
  });

  it("names a recipe to the draft route by key for a starter and by id for a row, never both", () => {
    expect(recipeRef(list[0])).toEqual({ recipe_key: "follow_up_email" });
    const row = { ...list[0], id: 7, source: "personal" as const, pack: null };
    expect(recipeRef(row)).toEqual({ recipe_id: 7 });
  });

  it("starts each choice at its default, or empty for text", () => {
    const email = list.find((r) => r.key === "follow_up_email")!;
    expect(defaultOptions(email)).toEqual({ recipient: "", tone: "Friendly" });
  });

  it("sends only the choices the person filled", () => {
    expect(optionsToSend({ recipient: "  ", tone: "Formal" })).toEqual({ tone: "Formal" });
    expect(optionsToSend({ recipient: " Daniel ", tone: "Friendly" })).toEqual({ recipient: "Daniel", tone: "Friendly" });
  });

  it("makes a key from a label", () => {
    expect(slug("Recipient or client name", [])).toBe("recipient_or_client_name");
    expect(slug("Tone", ["tone"])).toBe("tone_2");
    expect(slug("  ", [])).toBe("choice");
    expect(slug("1st look", [])).toBe("c_1st_look");
  });

  it("shows the server's words for the refusals written for the person, the fallback for the rest", () => {
    for (const code of ["brief_too_long", "no_text_layer", "too_many_pages", "unsupported", "unreadable", "import_busy", "too_many_recipes", "options_invalid"]) {
      expect(PERSON_CODES).toContain(code);
      expect(recipeErrorMessage(new ApiError(422, code, "Words for the person."))).toBe("Words for the person.");
    }
    expect(recipeErrorMessage(new ApiError(422, "validation_error", "Some fields are missing."))).toBe(
      "Something went wrong. Please try again.",
    );
    expect(recipeErrorMessage(new ApiError(429, "recipe_limit", "You have made 6 documents today. Try again tomorrow."))).toBe(
      "You have made 6 documents today. Try again tomorrow.",
    );
  });
});
