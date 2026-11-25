// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RecipesView } from "@/components/app/recipes-view";
import { ApiError } from "@/lib/api/client";
import type { Brief, Recipe, RecipeList } from "@/lib/api/types";
import { recipesLibraryBody, recipesLibraryCapture } from "./helpers";

// Fed the bodies captured from the running API (docs/fe-captures/recipes-library/): the list as an owner and
// as a member read it, the recipes made and changed, and the refusals. The `too_many_recipes` sentence is NOT
// captured (the shelf holds 100): its words are the ones in docs/fe-integration-guide-recipes-library.md.
const mocks = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn(), archive: vi.fn(), save: vi.fn(), importFile: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    recipes: { create: mocks.create, update: mocks.update, archive: mocks.archive },
    brief: { save: mocks.save, importFile: mocks.importFile },
  },
}));

const owner = recipesLibraryBody<RecipeList>("11-list-starters.json");
const asMember = recipesLibraryBody<RecipeList>("17-list-as-member.json");
const personal = recipesLibraryBody<Recipe>("12-create-personal.json");
const copy = recipesLibraryBody<Recipe>("13-create-copy-of-starter.json");
const updated = recipesLibraryBody<Recipe>("14-update.json");
const teamMade = recipesLibraryBody<Recipe>("15-create-team.json");
const brief = recipesLibraryBody<Brief>("39-brief-put.json");
const memberBrief = recipesLibraryBody<Brief>("40-brief-get-as-member.json");
const refusal = (file: string) => {
  const capture = recipesLibraryCapture(file);
  const { code, message } = (capture.response.body as { error: { code: string; message: string } }).error;
  return new ApiError(capture.response.status, code, message);
};
const withOwn = (list: RecipeList, ...rows: Recipe[]): RecipeList => ({ ...list, recipes: [...list.recipes, ...rows] });

beforeEach(() => Object.values(mocks).forEach((fn) => fn.mockReset()));
afterEach(cleanup);

const pick = (name: RegExp | string) => userEvent.click(screen.getByRole("button", { name }));
const form = () => screen.queryByRole("form", { name: "Recipe" }) ?? screen.getByRole("region", { name: "Recipe" });

const showStarters = () => userEvent.click(screen.getByRole("button", { name: /^Show the \d+ ready-made recipes/ }));
const list = () => screen.getByRole("region", { name: "Recipes" });

describe("the page", () => {
  it("says in one line what a recipe is", () => {
    render(<RecipesView initial={owner} brief={brief} />);
    expect(screen.getByText("A recipe tells Beyric what to write from a meeting. Pick one, or make your own.")).toBeTruthy();
  });

  it("puts the recipes before the brief", () => {
    render(<RecipesView initial={owner} brief={brief} />);
    const recipes = list();
    const box = screen.getByRole("heading", { name: "My brief" });
    expect(recipes.compareDocumentPosition(box) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("has a short welcome on the right until a recipe is picked", async () => {
    render(<RecipesView initial={owner} brief={brief} />);
    expect(screen.getByRole("heading", { name: "Pick a recipe" })).toBeTruthy();
    expect(screen.queryByRole("form", { name: "Recipe" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Recipe" })).toBeNull();
  });
});

describe("the list", () => {
  it("leads with the person's own and the team's recipes, and shuts the ready-made ones", async () => {
    render(<RecipesView initial={withOwn(asMember, personal)} brief={memberBrief} />);
    const heads = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(heads).toEqual(["My recipes", "Team recipes", "Ready-made recipes"]);
    for (const name of ["House proposal", "Admin recipe", "Weekly note"]) {
      expect(within(list()).getByRole("button", { name: new RegExp(name) })).toBeTruthy();
    }
    expect(within(list()).queryByRole("button", { name: /Follow-up email/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Show the \d+ ready-made recipes/ }).getAttribute("aria-expanded")).toBe("false");
  });

  it("opens the ready-made recipes under General and School", async () => {
    render(<RecipesView initial={withOwn(asMember, personal)} brief={memberBrief} />);
    await showStarters();
    expect(screen.getAllByRole("heading", { level: 4 }).map((h) => h.textContent)).toEqual(["General", "School"]);
    for (const name of ["Follow-up email", "Proposal", "Study guide", "Class FAQ"]) {
      expect(within(list()).getByRole("button", { name: new RegExp(name) })).toBeTruthy();
    }
    expect(screen.getByRole("button", { name: /^Hide the ready-made recipes/ }).getAttribute("aria-expanded")).toBe("true");
  });

  it("searches the ready-made recipes, and says when none match", async () => {
    render(<RecipesView initial={owner} brief={brief} />);
    await showStarters();
    const search = screen.getByRole("searchbox", { name: "Search the ready-made recipes" });
    await userEvent.type(search, "study");
    expect(within(list()).getByRole("button", { name: /Study guide/ })).toBeTruthy();
    expect(within(list()).queryByRole("button", { name: /Follow-up email/ })).toBeNull();
    await userEvent.clear(search);
    await userEvent.type(search, "zzzz");
    expect(screen.getByText(/No ready-made recipe matches/)).toBeTruthy();
  });

  it("says what My recipes is while it is empty, with one button that opens the ready-made ones", async () => {
    render(<RecipesView initial={owner} brief={brief} />);
    expect(screen.getByRole("heading", { name: "My recipes" })).toBeTruthy();
    expect(within(list()).getByText(/Only you can use these. Nothing here yet/)).toBeTruthy();
    expect(within(list()).queryByRole("button", { name: /Follow-up email/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Browse ready-made recipes" }));
    expect(within(list()).getByRole("button", { name: /Follow-up email/ })).toBeTruthy();
  });

  it("says what Team recipes is while it is empty in a team workspace", () => {
    render(<RecipesView initial={{ ...owner, team: { ...owner.team, is_personal: false, name: "Platform team" } }} brief={brief} />);
    expect(screen.getByRole("heading", { name: "Team recipes" })).toBeTruthy();
    expect(within(list()).getByText(/Recipes saved for everyone on Platform team. None yet: save one and it shows here/)).toBeTruthy();
  });

  it("tells a member who may save one for the team", () => {
    render(<RecipesView initial={{ ...asMember, recipes: asMember.recipes.filter((r) => r.source !== "team") }} brief={memberBrief} />);
    expect(within(list()).getByText(/None yet: an owner or admin can save one/)).toBeTruthy();
  });

  it("has no Team recipes in a personal space (there is no team to save one for)", () => {
    render(<RecipesView initial={{ ...owner, team: { ...owner.team, is_personal: true } }} brief={brief} />);
    expect(screen.queryByRole("heading", { name: "Team recipes" })).toBeNull();
  });
});

describe("a ready-made recipe", () => {
  const open = async (name: RegExp = /Follow-up email/) => {
    await showStarters();
    await pick(name);
  };

  it("is described in words, not as greyed-out fields", async () => {
    render(<RecipesView initial={owner} brief={brief} />);
    await open();
    const f = form();
    expect(within(f).getByRole("heading", { name: "Follow-up email" })).toBeTruthy();
    expect(within(f).getByText("A ready-made recipe from Beyric.")).toBeTruthy();
    expect(within(f).getByText("Makes an email from one meeting.")).toBeTruthy();
    expect(within(f).getByText(/Beyric will ask you/)).toBeTruthy();
    expect(within(f).getByText(/Recipient or client name, Tone \(Friendly or Formal\)/)).toBeTruthy();
    expect(within(f).getByText("Your meeting, and my brief if there is one.")).toBeTruthy();
    expect(within(f).queryByLabelText("Name")).toBeNull();
    expect(within(f).queryByRole("textbox")).toBeNull();
    expect(within(f).queryByRole("combobox")).toBeNull();
    expect(within(f).queryByRole("checkbox")).toBeNull();
    expect(screen.queryByText(/A starter cannot be changed/)).toBeNull();
  });

  it("keeps the whole instruction one click away", async () => {
    render(<RecipesView initial={owner} brief={brief} />);
    await open();
    expect(within(form()).getByText("Read the whole instruction")).toBeTruthy();
    expect(form().querySelector(".recipe-instruction")?.textContent).toBe(owner.recipes[0].instruction);
  });

  it("is used with one click: the person's own copy is made and opened for changes", async () => {
    mocks.create.mockResolvedValue(copy);
    render(<RecipesView initial={owner} brief={brief} />);
    await open();
    await userEvent.click(within(form()).getByRole("button", { name: "Use this recipe" }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalledTimes(1));
    expect(mocks.create.mock.calls[0][0]).toEqual({
      key: "follow_up_email",
      name: "My Follow-up email",
      instruction: owner.recipes[0].instruction,
      output_type: "email",
      options: owner.recipes[0].options,
      scope: "one",
      uses_brief: true,
      visibility: "personal",
    });
    expect(await screen.findByText(/This is your own copy/)).toBeTruthy();
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(copy.name);
    const mine = screen.getByRole("heading", { name: "My recipes" });
    expect(within(mine.parentElement as HTMLElement).getByRole("button", { name: new RegExp(copy.name) })).toBeTruthy();
    expect(screen.getByText(/Copied from Follow-up email/)).toBeTruthy();
  });

  it("shows the server's words when the copy cannot be made, and stays on the recipe", async () => {
    mocks.create.mockRejectedValue(new ApiError(422, "too_many_recipes", "Keep at most 100 recipes; archive one."));
    render(<RecipesView initial={owner} brief={brief} />);
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Use this recipe" }));
    expect(await screen.findByText("Keep at most 100 recipes; archive one.")).toBeTruthy();
    expect(within(form()).getByRole("button", { name: "Use this recipe" })).toBeTruthy();
  });

  it("can still be changed before saving, which is where it can be made the team's", async () => {
    render(<RecipesView initial={owner} brief={brief} />);
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Change it before saving" }));
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Follow-up email (copy)");
    expect(screen.getByRole("radio", { name: "Only me" })).toBeTruthy();
  });
});

describe("a recipe of your own", () => {
  it("is written with a name and an instruction, and listed once saved", async () => {
    mocks.create.mockResolvedValue(personal);
    render(<RecipesView initial={owner} brief={brief} />);
    await userEvent.click(screen.getByRole("button", { name: "New recipe" }));
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.type(screen.getByLabelText("Name"), personal.name);
    await userEvent.type(screen.getByLabelText("Instruction"), personal.instruction);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalled());
    expect(mocks.create.mock.calls[0][0]).toEqual({
      name: personal.name,
      instruction: personal.instruction,
      output_type: "document",
      options: {},
      scope: "one",
      uses_brief: true,
      visibility: "personal",
    });
    const mine = await screen.findByRole("heading", { name: "My recipes" });
    expect(within(mine.parentElement as HTMLElement).getByRole("button", { name: /Weekly note/ })).toBeTruthy();
  });

  it("offers who can use it to an owner or admin, and sends the team when chosen", async () => {
    mocks.create.mockResolvedValue(teamMade);
    render(<RecipesView initial={owner} brief={brief} />);
    await userEvent.click(screen.getByRole("button", { name: "New recipe" }));
    expect((screen.getByRole("radio", { name: "Only me" }) as HTMLInputElement).checked).toBe(true);
    await userEvent.click(screen.getByRole("radio", { name: "Everyone on My team" }));
    await userEvent.type(screen.getByLabelText("Name"), teamMade.name);
    await userEvent.type(screen.getByLabelText("Instruction"), teamMade.instruction);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalled());
    expect(mocks.create.mock.calls[0][0].visibility).toBe("team");
    expect(await screen.findByRole("heading", { name: "Team recipes" })).toBeTruthy();
  });

  it("does not offer the team to a member, who can still write their own", async () => {
    render(<RecipesView initial={asMember} brief={memberBrief} />);
    await userEvent.click(screen.getByRole("button", { name: "New recipe" }));
    expect(screen.queryByRole("radio", { name: "Only me" })).toBeNull();
    expect(screen.queryByRole("radio", { name: /Everyone on/ })).toBeNull();
  });

  it("is changed and saved in place", async () => {
    mocks.update.mockResolvedValue(updated);
    render(<RecipesView initial={withOwn(owner, personal)} brief={brief} />);
    await pick(/Weekly note/);
    const name = screen.getByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Monthly note");
    await userEvent.click(screen.getByRole("checkbox", { name: "Use my brief" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocks.update).toHaveBeenCalled());
    expect(mocks.update).toHaveBeenCalledWith(personal.id, {
      name: "Monthly note",
      instruction: personal.instruction,
      output_type: "document",
      options: personal.options,
      scope: "one",
      uses_brief: false,
    });
    expect(await screen.findByRole("button", { name: /Monthly note/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Weekly note/ })).toBeNull();
  });

  it("is archived after asking, and the drafts made from it are said to stay", async () => {
    mocks.archive.mockResolvedValue(undefined);
    render(<RecipesView initial={withOwn(owner, personal)} brief={brief} />);
    await pick(/Weekly note/);
    await userEvent.click(screen.getByRole("button", { name: "Archive recipe" }));
    expect(screen.getByText(/Drafts already made from it are kept/)).toBeTruthy();
    expect(mocks.archive).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Archive" }));
    await waitFor(() => expect(mocks.archive).toHaveBeenCalledWith(personal.id));
    await waitFor(() => expect(screen.queryByRole("button", { name: /Weekly note/ })).toBeNull());
  });

  it("asking not to archive keeps it", async () => {
    render(<RecipesView initial={withOwn(owner, personal)} brief={brief} />);
    await pick(/Weekly note/);
    await userEvent.click(screen.getByRole("button", { name: "Archive recipe" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(mocks.archive).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Archive recipe" })).toBeTruthy();
  });
});

describe("a team recipe read by a member", () => {
  it("is described in words, with a way to make a copy of their own", async () => {
    render(<RecipesView initial={asMember} brief={memberBrief} />);
    await pick(/House proposal/);
    const f = form();
    expect(within(f).getByRole("heading", { name: "House proposal" })).toBeTruthy();
    expect(within(f).getByText(/A recipe for everyone on .*Only an owner or admin can change it/)).toBeTruthy();
    expect(within(f).queryByRole("textbox")).toBeNull();
    expect(within(f).queryByRole("button", { name: "Save" })).toBeNull();
    expect(within(f).queryByRole("button", { name: "Archive recipe" })).toBeNull();
    expect(within(f).getByText(teamMade.instruction)).toBeTruthy();
    expect(within(f).getByRole("button", { name: "Use this recipe" })).toBeTruthy();
  });
});

describe("the choices a recipe offers", () => {
  it("are added, named and given their words", async () => {
    mocks.create.mockResolvedValue(personal);
    render(<RecipesView initial={owner} brief={brief} />);
    await userEvent.click(screen.getByRole("button", { name: "New recipe" }));
    await userEvent.type(screen.getByLabelText("Name"), "Note");
    await userEvent.type(screen.getByLabelText("Instruction"), "Write it.");
    await userEvent.click(screen.getByRole("button", { name: "Add a choice" }));
    await userEvent.type(screen.getByLabelText("Choice 1 name"), "Audience");
    await userEvent.click(screen.getByRole("button", { name: "Add a choice" }));
    await userEvent.type(screen.getByLabelText("Choice 2 name"), "Length");
    await userEvent.selectOptions(screen.getByLabelText("Choice 2 kind"), "choice");
    await userEvent.type(screen.getByLabelText("Choice 2 words"), "Short, Long ,");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalled());
    expect(mocks.create.mock.calls[0][0].options).toEqual({
      audience: { label: "Audience", kind: "text", choices: [], default: null },
      length: { label: "Length", kind: "choice", choices: ["Short", "Long"], default: null },
    });
  });

  it("keep their key when an existing one is renamed, and can be removed", async () => {
    mocks.update.mockResolvedValue(personal);
    render(<RecipesView initial={withOwn(owner, personal)} brief={brief} />);
    await pick(/Weekly note/);
    const label = screen.getByLabelText("Choice 1 name");
    expect((label as HTMLInputElement).value).toBe("Client");
    await userEvent.clear(label);
    await userEvent.type(label, "Client name");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(mocks.update).toHaveBeenCalled());
    expect(Object.keys(mocks.update.mock.calls[0][1].options)).toEqual(["who"]);
    expect(mocks.update.mock.calls[0][1].options.who.label).toBe("Client name");
    await userEvent.click(screen.getByRole("button", { name: "Remove choice 1" }));
    expect(screen.queryByLabelText("Choice 1 name")).toBeNull();
  });

  it("offer a word choice only with words to choose between", async () => {
    render(<RecipesView initial={owner} brief={brief} />);
    await userEvent.click(screen.getByRole("button", { name: "New recipe" }));
    await userEvent.type(screen.getByLabelText("Name"), "Note");
    await userEvent.type(screen.getByLabelText("Instruction"), "Write it.");
    await userEvent.click(screen.getByRole("button", { name: "Add a choice" }));
    await userEvent.type(screen.getByLabelText("Choice 1 name"), "Tone");
    await userEvent.selectOptions(screen.getByLabelText("Choice 1 kind"), "choice");
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Give each word choice at least one word/)).toBeTruthy();
  });

  it("stop at six", async () => {
    render(<RecipesView initial={owner} brief={brief} />);
    await userEvent.click(screen.getByRole("button", { name: "New recipe" }));
    for (let i = 0; i < 6; i += 1) await userEvent.click(screen.getByRole("button", { name: "Add a choice" }));
    expect((screen.getByRole("button", { name: "Add a choice" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("refusals", () => {
  const fillAndSave = async () => {
    await userEvent.click(screen.getByRole("button", { name: "New recipe" }));
    await userEvent.type(screen.getByLabelText("Name"), "Note");
    await userEvent.type(screen.getByLabelText("Instruction"), "Write it.");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
  };

  it("shows the server's words for a team recipe a member may not write", async () => {
    mocks.create.mockRejectedValue(refusal("18-create-team-as-member.json"));
    render(<RecipesView initial={owner} brief={brief} />);
    await fillAndSave();
    expect(await screen.findByText("Only an owner or admin of the team can do that.")).toBeTruthy();
    expect(screen.queryByText("Saved")).toBeNull();
  });

  it("shows the server's words for choices it will not take", async () => {
    mocks.create.mockRejectedValue(refusal("31-create-bad-options-rule.json"));
    render(<RecipesView initial={owner} brief={brief} />);
    await fillAndSave();
    expect(await screen.findByText("a choice offers between one and six short words")).toBeTruthy();
  });

  it("shows a full shelf in the server's words", async () => {
    mocks.create.mockRejectedValue(new ApiError(422, "too_many_recipes", "Keep at most 100 recipes; archive one."));
    render(<RecipesView initial={owner} brief={brief} />);
    await fillAndSave();
    expect(await screen.findByText("Keep at most 100 recipes; archive one.")).toBeTruthy();
  });

  it("says a recipe is gone when another tab archived it", async () => {
    mocks.update.mockRejectedValue(new ApiError(404, "not_found", "No such recipe."));
    render(<RecipesView initial={withOwn(owner, personal)} brief={brief} />);
    await pick(/Weekly note/);
    // Save waits for a change: edit, then save
    const name = screen.getByLabelText("Name");
    await userEvent.type(name, "!");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("That recipe is gone. It may have been archived in another tab.")).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("button", { name: /Weekly note/ })).toBeNull());
  });
});

describe("the brief on the page", () => {
  it("is called my brief in the person's own space, with a plain line about it", () => {
    render(<RecipesView initial={owner} brief={brief} />);
    expect(screen.getByRole("heading", { name: "My brief" })).toBeTruthy();
    expect(screen.getByText(/Facts about your business that Beyric can use/)).toBeTruthy();
  });

  it("is called the workspace brief in a team's", () => {
    render(<RecipesView initial={asMember} brief={memberBrief} />);
    expect(screen.getByRole("heading", { name: "Workspace brief" })).toBeTruthy();
  });
});
