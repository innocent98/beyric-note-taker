// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MakeDialog } from "@/components/app/make-dialog";
import { ApiError } from "@/lib/api/client";
import type { Brief, DocumentDraft, Recipe, RecipeList } from "@/lib/api/types";
import { recipesBody, recipesCapture, recipesLibraryBody } from "./helpers";

// Fed the bodies captured from the running API: the list and the brief
// (docs/fe-captures/recipes-library/), the 202 answer and the daily-limit refusal
// (docs/fe-captures/recipes/). The plan_inactive sentence is NOT captured (it needs enforce mode and a
// person with no plan): its words are the ones in docs/fe-integration-guide-recipes.md.
const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  brief: vi.fn(),
  teams: vi.fn(),
  make: vi.fn(),
  push: vi.fn(),
  series: vi.fn(),
  resolve: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    recipes: { list: mocks.list, resolvePicker: mocks.resolve },
    series: { list: mocks.series },
    projects: { list: vi.fn().mockResolvedValue({ projects: [] }) },
    sprints: { list: vi.fn().mockResolvedValue({ sprints: [] }) },
    meetings: { list: vi.fn().mockResolvedValue({ meetings: [] }) },
    brief: { get: mocks.brief },
    teams: { list: mocks.teams },
    documents: { create: mocks.make },
  },
}));

const starters = recipesLibraryBody<RecipeList>("11-list-starters.json");
const withTeam = recipesLibraryBody<RecipeList>("17-list-as-member.json");
const personal = recipesLibraryBody<Recipe>("12-create-personal.json");
const emptyBrief = recipesLibraryBody<Brief>("38-brief-empty.json");
const savedBrief = recipesLibraryBody<Brief>("39-brief-put.json");
const memberBrief = recipesLibraryBody<Brief>("40-brief-get-as-member.json");
const started = recipesBody<DocumentDraft>("07-create.json");
const overTheCap = recipesCapture("39-create-over-the-cap.json").response.body as { error: { code: string; message: string } };
const PLAN_INACTIVE =
  "Making documents needs an active plan or trial. You can still read, search and export your notes, and use your tasks and decisions.";

const team = (id: number, name: string, extra = {}) => ({
  id,
  name,
  role: "owner" as const,
  member_count: 2,
  is_personal: false,
  is_default: id === 2,
  can_manage: true,
  ...extra,
});

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
  mocks.list.mockResolvedValue(starters);
  mocks.brief.mockResolvedValue(savedBrief);
  mocks.teams.mockResolvedValue({ teams: [team(2, "My team")], can_create_more: 0 });
  mocks.make.mockResolvedValue(started);
  mocks.series.mockResolvedValue({ timezone: "UTC", series: [{ id: 3, title: "Monday planning" }] });
  mocks.resolve.mockResolvedValue({
    meetings: [
      { id: 12, title: "Review", day: "2026-11-09" },
      { id: 11, title: "Kick-off", day: "2026-11-02" },
    ],
    left_out_count: 1,
    left_out_message: "1 meeting was left out because you cannot read it or it is not ready.",
    capped: false,
    max_meetings: 12,
  });
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
    this.removeAttribute("open");
  };
});
afterEach(cleanup);

async function open(props: Partial<Parameters<typeof MakeDialog>[0]> = {}) {
  const onClose = vi.fn();
  render(<MakeDialog open meetingId={7} title="Onboarding call" onClose={onClose} {...props} />);
  await screen.findByRole("radio", { name: /Follow-up email/ });
  return { onClose };
}
const make = () => userEvent.click(screen.getByRole("button", { name: "Make draft" }));

describe("the recipes", () => {
  it("lists the starters under General and School, in order", async () => {
    await open();
    expect(screen.getByRole("heading", { name: "Make from this meeting" })).toBeTruthy();
    expect(screen.getByText("From Onboarding call.")).toBeTruthy();
    const general = screen.getByRole("group", { name: "General" });
    expect(within(general).getAllByRole("radio").map((r) => r.getAttribute("value"))).toEqual([
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
    const school = screen.getByRole("group", { name: "School" });
    expect(within(school).getAllByRole("radio")).toHaveLength(8);
    expect(screen.queryByRole("group", { name: "My recipes" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Team recipes" })).toBeNull();
  });

  it("adds the team's recipes and the person's own after the starters", async () => {
    mocks.list.mockResolvedValue({ ...withTeam, recipes: [...withTeam.recipes, personal] });
    await open();
    const teams = screen.getByRole("group", { name: "Team recipes" });
    expect(within(teams).getAllByRole("radio").map((r) => r.getAttribute("aria-label") ?? r.closest("label")?.textContent)).toEqual([
      expect.stringContaining("House proposal"),
      expect.stringContaining("Admin recipe"),
    ]);
    const mine = screen.getByRole("group", { name: "My recipes" });
    expect(within(mine).getByRole("radio", { name: /Weekly note/ })).toBeTruthy();
  });

  it("starts on the first recipe and shows its choices at their defaults", async () => {
    await open();
    expect((screen.getByRole("radio", { name: /Follow-up email/ }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("Recipient or client name") as HTMLInputElement).value).toBe("");
    expect((screen.getByRole("radio", { name: "Friendly" }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByRole("radio", { name: "Formal" })).toBeTruthy();
  });

  it("shows the choices of the recipe picked, and none for one that has none", async () => {
    await open();
    await userEvent.click(screen.getByRole("radio", { name: /Marketing post/ }));
    expect(screen.getByLabelText("Audience")).toBeTruthy();
    expect(screen.getByRole("radio", { name: "Medium" })).toBeTruthy();
    expect(screen.queryByLabelText("Recipient or client name")).toBeNull();
    await userEvent.click(screen.getByRole("radio", { name: /Decision memo/ }));
    expect(screen.queryByLabelText("Audience")).toBeNull();
  });
});

describe("the brief and the team", () => {
  it("says whose brief the draft will use and how much it holds", async () => {
    await open();
    expect(await screen.findByText(/My team/, { selector: ".mk-brief-name" })).toBeTruthy();
    const use = screen.getByRole("checkbox", { name: /Use the brief of/ }) as HTMLInputElement;
    expect(use.checked).toBe(true);
    expect(screen.getByText(`${savedBrief.text.length} characters`)).toBeTruthy();
  });

  it("sends use_brief false when the person unticks it", async () => {
    await open();
    await userEvent.click(await screen.findByRole("checkbox", { name: /Use the brief of/ }));
    await make();
    await waitFor(() => expect(mocks.make).toHaveBeenCalled());
    expect(mocks.make.mock.calls[0][0].use_brief).toBe(false);
  });

  it("a team with no brief says so, offers to write one to a manager, and sends nothing about it", async () => {
    mocks.brief.mockResolvedValue(emptyBrief);
    await open();
    expect(await screen.findByText(/has no brief yet/)).toBeTruthy();
    expect(screen.queryByRole("checkbox", { name: /Use the brief of/ })).toBeNull();
    expect(screen.getByRole("link", { name: "Write one" }).getAttribute("href")).toBe("/recipes");
  });

  it("a member reads that there is a brief and is not offered to write one", async () => {
    mocks.brief.mockResolvedValue({ ...memberBrief, text: "" });
    await open();
    await screen.findByText(/has no brief yet/);
    expect(screen.queryByRole("link", { name: "Write one" })).toBeNull();
  });

  it("with several teams offers a switch, and reloads the recipes and the brief for the one chosen", async () => {
    mocks.teams.mockResolvedValue({ teams: [team(2, "My team"), team(8, "Acme", { role: "member", can_manage: false })], can_create_more: 1 });
    await open();
    const select = (await screen.findByLabelText("Team")) as HTMLSelectElement;
    expect(select.value).toBe("2");
    mocks.brief.mockResolvedValue({ ...savedBrief, team: { id: 8, name: "Acme", is_personal: false, can_manage: false }, text: "Acme prices." });
    await userEvent.selectOptions(select, "8");
    await waitFor(() => expect(mocks.list).toHaveBeenLastCalledWith(8));
    expect(mocks.brief).toHaveBeenLastCalledWith(8);
    expect(await screen.findByText("12 characters")).toBeTruthy();
    await make();
    await waitFor(() => expect(mocks.make).toHaveBeenCalled());
    expect(mocks.make.mock.calls[0][1]).toBe(8);
  });

  it("with one team shows no switch", async () => {
    await open();
    await screen.findByText(/My team/, { selector: ".mk-brief-name" });
    expect(screen.queryByLabelText("Team")).toBeNull();
  });
});

describe("making the draft", () => {
  it("asks for a starter by key with the meeting, the choices and the brief, then opens the draft", async () => {
    await open();
    await userEvent.type(screen.getByLabelText("Recipient or client name"), " Daniel ");
    await userEvent.click(screen.getByRole("radio", { name: "Formal" }));
    await make();
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(`/documents/${started.id}`));
    expect(mocks.make).toHaveBeenCalledTimes(1);
    expect(mocks.make.mock.calls[0][0]).toEqual({
      recipe_key: "follow_up_email",
      meeting_ids: [7],
      options: { recipient: "Daniel", tone: "Formal" },
      use_brief: true,
    });
  });

  it("asks for a recipe row by id", async () => {
    mocks.list.mockResolvedValue({ ...starters, recipes: [...starters.recipes, personal] });
    await open();
    await userEvent.click(screen.getByRole("radio", { name: /Weekly note/ }));
    await userEvent.type(screen.getByLabelText("Client"), "Daniel");
    await make();
    await waitFor(() => expect(mocks.make).toHaveBeenCalled());
    expect(mocks.make.mock.calls[0][0]).toMatchObject({ recipe_id: personal.id, meeting_ids: [7], options: { who: "Daniel" } });
    expect(mocks.make.mock.calls[0][0]).not.toHaveProperty("recipe_key");
  });

  it("presses once, asks once", async () => {
    let finish: (value: DocumentDraft) => void = () => undefined;
    mocks.make.mockReturnValue(new Promise<DocumentDraft>((resolve) => (finish = resolve)));
    await open();
    const button = screen.getByRole("button", { name: "Make draft" });
    await userEvent.dblClick(button);
    expect(mocks.make).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Making..." })).toBeTruthy();
    finish(started);
    await waitFor(() => expect(mocks.push).toHaveBeenCalledTimes(1));
  });

  it("shows the daily limit and stays open to try again later", async () => {
    mocks.make.mockRejectedValue(new ApiError(429, overTheCap.error.code, overTheCap.error.message));
    const { onClose } = await open();
    await make();
    expect(await screen.findByText(overTheCap.error.message)).toBeTruthy();
    expect(mocks.push).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "Make draft" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows the plan refusal with the way to the plans", async () => {
    mocks.make.mockRejectedValue(new ApiError(403, "plan_inactive", PLAN_INACTIVE));
    await open();
    await make();
    expect(await screen.findByText(PLAN_INACTIVE, { exact: false })).toBeTruthy();
    expect(screen.getByRole("link", { name: "See plans" })).toBeTruthy();
  });

  it("cancel closes without asking for anything", async () => {
    const { onClose } = await open();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalled();
    expect(mocks.make).not.toHaveBeenCalled();
  });

  it("says when the recipes could not be loaded and tries again", async () => {
    mocks.list.mockRejectedValueOnce(new ApiError(0, "network_error", "Could not reach the server. Check your connection and try again."));
    render(<MakeDialog open meetingId={7} title="Onboarding call" onClose={vi.fn()} />);
    expect(await screen.findByText("Could not reach the server. Check your connection and try again.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Make draft" }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("radio", { name: /Follow-up email/ })).toBeTruthy();
  });
});

describe("a recipe across meetings", () => {
  it("mounts the meeting picker in a marked place, only for a recipe that reads several meetings", async () => {
    await open();
    expect(screen.queryByRole("region", { name: "Meetings" })).toBeNull();
    await userEvent.click(screen.getByRole("radio", { name: /Status report/ }));
    const slot = screen.getByRole("region", { name: "Meetings" });
    expect(within(slot).getByLabelText("Choose meetings by")).toBeTruthy();
    await userEvent.click(screen.getByRole("radio", { name: /Follow-up email/ }));
    expect(screen.queryByRole("region", { name: "Meetings" })).toBeNull();
  });

  it("waits for a choice, then sends the previewed meetings and the choice, and shows what was left out", async () => {
    await open();
    await userEvent.click(screen.getByRole("radio", { name: /Status report/ }));
    expect((screen.getByRole("button", { name: "Make draft" }) as HTMLButtonElement).disabled).toBe(true);
    await screen.findByRole("option", { name: "Monday planning" });
    await userEvent.selectOptions(screen.getByLabelText("Recurring meeting"), "3");
    expect(await screen.findByText("Review")).toBeTruthy();
    expect(screen.getByText(/1 meeting was left out because you cannot read it/)).toBeTruthy();
    await waitFor(() => expect((screen.getByRole("button", { name: "Make draft" }) as HTMLButtonElement).disabled).toBe(false));
    await make();
    await waitFor(() => expect(mocks.make).toHaveBeenCalled());
    expect(mocks.make.mock.calls[0][0]).toMatchObject({
      recipe_key: "status_report",
      meeting_ids: [12, 11],
      picker: { series_id: 3 },
    });
    expect(mocks.resolve).toHaveBeenCalledWith({ series_id: 3 }, null);
  });

  it("sends no picker for a recipe that reads one meeting", async () => {
    await open();
    await make();
    await waitFor(() => expect(mocks.make).toHaveBeenCalled());
    expect(mocks.make.mock.calls[0][0].picker).toBeUndefined();
    expect(mocks.make.mock.calls[0][0].meeting_ids).toEqual([7]);
  });

  it("asks the picker about the team chosen in the dialog", async () => {
    mocks.teams.mockResolvedValue({ teams: [team(2, "My team"), team(5, "Other team")], can_create_more: 0 });
    await open();
    await screen.findByLabelText("Team");
    await userEvent.selectOptions(screen.getByLabelText("Team"), "5");
    await userEvent.click(await screen.findByRole("radio", { name: /Status report/ }));
    await screen.findByRole("option", { name: "Monday planning" });
    expect(mocks.series).toHaveBeenLastCalledWith(5);
  });

  it("mounts the picker it is given, hands it this meeting and sends what it chooses", async () => {
    const picker = vi.fn(({ meetingIds, onChange }: { meetingIds: number[]; onChange: (ids: number[]) => void; team: number | null }): ReactNode => (
      <button type="button" onClick={() => onChange([...meetingIds, 9, 12])}>
        Pick more ({meetingIds.join(",")})
      </button>
    ));
    await open({ renderMeetingPicker: picker });
    await userEvent.click(screen.getByRole("radio", { name: /Status report/ }));
    await userEvent.click(screen.getByRole("button", { name: "Pick more (7)" }));
    await make();
    await waitFor(() => expect(mocks.make).toHaveBeenCalled());
    expect(mocks.make.mock.calls[0][0]).toMatchObject({ recipe_key: "status_report", meeting_ids: [7, 9, 12] });
  });

  it("a recipe for one meeting uses the page's own id whatever the picker held for another", async () => {
    await open({
      renderMeetingPicker: ({ meetingIds, onChange }) => (
        <button type="button" onClick={() => onChange([...meetingIds, 9, 12])}>
          Pick more
        </button>
      ),
    });
    await userEvent.click(screen.getByRole("radio", { name: /Status report/ }));
    await userEvent.click(screen.getByRole("button", { name: "Pick more" }));
    await userEvent.click(screen.getByRole("radio", { name: /Follow-up email/ }));
    expect(screen.queryByRole("button", { name: "Pick more" })).toBeNull();
    await make();
    await waitFor(() => expect(mocks.make).toHaveBeenCalled());
    expect(mocks.make.mock.calls[0][0].meeting_ids).toEqual([7]);
  });
});
