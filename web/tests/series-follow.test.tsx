// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { join } from "node:path";
import { RecurringCards } from "@/components/app/recurring-cards";
import { SeriesView } from "@/components/app/series-view";
import type { SeriesList, SeriesPage } from "@/lib/api/types";
import { REPO, read } from "./helpers";

// Recurring meetings follow the meetings (docs/fe-integration-guide-series-teams.md): the cards and the
// page say which team a row is, a personal row has no "Next meeting" and no "Send agenda", and an emptied
// row says so in plain words. Every body is one captured from the running app
// (docs/fe-captures/series-follow/, made by scripts/capture_series_follow.py).

const mocks = vi.hoisted(() => ({ list: vi.fn(), tasksList: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    series: { list: mocks.list, get: vi.fn(), addAgenda: vi.fn(), removeAgenda: vi.fn(), sendAgenda: vi.fn() },
    questions: { update: vi.fn() },
    tasks: { list: mocks.tasksList },
  },
}));

const body = <T,>(file: string) =>
  JSON.parse(read(join(REPO, "docs/fe-captures/series-follow", file))).response.body as T;
const teamPage = body<SeriesPage>("24-page-ada-x.json");
const personalPage = body<SeriesPage>("25-page-ada-personal.json");
const emptiedPersonalPage = body<SeriesPage>("41-page-ada-personal-after-share.json");
const teamCards = body<SeriesList>("14-cards-ada-x.json");
const personalCards = body<SeriesList>("15-cards-ada-personal.json");
const emptyCards = body<SeriesList>("21-cards-quinn-personal-empty.json");

beforeEach(() => {
  cleanup();
  mocks.list.mockReset();
  mocks.tasksList.mockReset();
  mocks.tasksList.mockResolvedValue({ people: [] });
});

describe("the capture is what these tests think it is", () => {
  it("is the team's row, a personal row and an emptied personal row", () => {
    expect(teamPage.team).toMatchObject({ name: "Platform team", is_personal: false });
    expect(personalPage.team).toMatchObject({ name: "My team", is_personal: true });
    expect(personalPage.next_text).toBeNull();
    expect(emptiedPersonalPage.past_meetings).toEqual([]);
    expect(emptyCards.series).toEqual([]);
  });
});

describe("the Series page of a team row, switch on", () => {
  it("says the team and what the page lists, and keeps the next meeting and the button", () => {
    render(<SeriesView initial={teamPage} multiTeam />);
    expect(screen.getByText(`${teamPage.cadence} · Google Meet · Platform team`)).toBeTruthy();
    expect(screen.getByText("The meetings of this series that are shared with Platform team.")).toBeTruthy();
    expect(screen.getByText("Next meeting")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send agenda to attendees" })).toBeTruthy();
  });

  it("asks for the task drawer's people in the row's team, not the tab's", async () => {
    render(<SeriesView initial={teamPage} multiTeam />);
    await userEvent.click(screen.getByText("Follow up on A1 shared"));
    await waitFor(() => expect(mocks.tasksList).toHaveBeenCalledWith({ limit: 1 }, teamPage.team.id));
  });
});

describe("the Series page of a personal row, switch on", () => {
  it("is called Personal, whatever the team is named, and says it lists private meetings", () => {
    render(<SeriesView initial={personalPage} multiTeam />);
    expect(screen.getByText(`${personalPage.cadence} · Google Meet · Personal`)).toBeTruthy();
    expect(screen.queryByText("My team")).toBeNull();
    expect(
      screen.getByText("Your private meetings of this series. The ones you share with a team are on that team's page."),
    ).toBeTruthy();
  });

  it("draws no Next meeting box and no Send agenda button", () => {
    render(<SeriesView initial={personalPage} multiTeam />);
    expect(screen.queryByText("Next meeting")).toBeNull();
    expect(screen.queryByRole("button", { name: "Send agenda to attendees" })).toBeNull();
    // the rest of the page is there: the tiles, its meeting, its question
    expect(screen.getByText("Done since last meeting")).toBeTruthy();
    expect(screen.getByText("Who owns A2 private?")).toBeTruthy();
    expect(screen.queryByText("Keeps coming up")).toBeNull();
  });

  it("says in plain words that an emptied row has no meeting left, and has no gaps", () => {
    render(<SeriesView initial={emptiedPersonalPage} multiTeam />);
    expect(screen.getByText("None of your private meetings are in this series right now.")).toBeTruthy();
    expect(screen.getByText("Nobody yet.")).toBeTruthy();
    expect(screen.getByText("Everything from last time is done.")).toBeTruthy();
    expect(screen.getByText("No open questions.")).toBeTruthy();
    expect(screen.getByText("Nothing finished yet. Completed tasks will show here.")).toBeTruthy();
  });

  it("says a team row with no meeting is about that team", () => {
    render(<SeriesView initial={{ ...teamPage, past_meetings: [], attendees: [] }} multiTeam />);
    expect(screen.getByText("No meeting of this series is shared with Platform team right now.")).toBeTruthy();
  });
});

describe("the Series page with the switch off", () => {
  it("is the page it was: no team, no scope line, the old empty words, the button on a personal row too", () => {
    render(<SeriesView initial={{ ...emptiedPersonalPage }} />);
    expect(screen.queryByText(/Personal/)).toBeNull();
    expect(screen.queryByText(/private meetings/)).toBeNull();
    expect(screen.getByText("No meetings recorded in this series yet.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send agenda to attendees" })).toBeTruthy();
  });

  it("asks for the task drawer's people with no team of its own", async () => {
    render(<SeriesView initial={teamPage} />);
    await userEvent.click(screen.getByText("Follow up on A1 shared"));
    await waitFor(() => expect(mocks.tasksList).toHaveBeenCalledWith({ limit: 1 }, undefined));
  });
});

describe("the Recurring cards", () => {
  const show = async (answer: SeriesList, multiTeam: boolean) => {
    mocks.list.mockReturnValue(Promise.resolve(answer));
    const view = render(<RecurringCards multiTeam={multiTeam} />);
    await waitFor(() => expect(mocks.list).toHaveBeenCalled());
    return view;
  };

  it("say which team a row belongs to while the switch is on", async () => {
    await show(teamCards, true);
    const standup = (await screen.findByText("Standup")).closest("li")!;
    expect(within(standup).getByText(`${teamCards.series[1].cadence} · Platform team`)).toBeTruthy();
  });

  it("call a personal row Personal and draw no Next chip on it", async () => {
    await show(personalCards, true);
    const standup = (await screen.findByText("Standup")).closest("li")!;
    expect(within(standup).getByText(`${personalCards.series[0].cadence} · Personal`)).toBeTruthy();
    expect(within(standup).queryByText(/^Next:/)).toBeNull();
    expect(within(standup).getByText("1 unfinished task")).toBeTruthy();
  });

  it("name no team with the switch off", async () => {
    await show(teamCards, false);
    const standup = (await screen.findByText("Standup")).closest("li")!;
    expect(within(standup).queryByText(/Platform team/)).toBeNull();
    expect(within(standup).getByText(teamCards.series[1].cadence)).toBeTruthy();
  });

  it("draw nothing at all when the list is empty", async () => {
    const view = await show(emptyCards, true);
    await waitFor(() => expect(mocks.list).toHaveBeenCalled());
    expect(view.container.innerHTML).toBe("");
  });
});
