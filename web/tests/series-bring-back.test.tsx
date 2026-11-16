// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { join } from "node:path";
import { SeriesView } from "@/components/app/series-view";
import { SettingsRemovedRecurring } from "@/components/app/settings-removed-recurring";
import { ApiError } from "@/lib/api/client";
import type { BroughtBack, RemovedList, SeriesPage } from "@/lib/api/types";
import { REPO, read } from "./helpers";

// "Removed recurring meetings" with "Bring back" in Settings, and the hint on a Personal Series page
// (LEAD-DESIGNED; docs/fe-integration-guide-series-teams.md section 12). Every list, page and answer is one
// captured from the running app (docs/fe-captures/series-follow/107 to 128, scripts/capture_series_follow.py).

const mocks = vi.hoisted(() => ({ removed: vi.fn(), bringBack: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    series: {
      removed: mocks.removed,
      bringBack: mocks.bringBack,
      get: vi.fn(),
      addAgenda: vi.fn(),
      removeAgenda: vi.fn(),
      sendAgenda: vi.fn(),
      remove: vi.fn(),
    },
    questions: { update: vi.fn() },
    tasks: { list: vi.fn().mockResolvedValue({ people: [] }) },
  },
}));

const capture = (file: string) => JSON.parse(read(join(REPO, "docs/fe-captures/series-follow", file)));
const personalList = capture("112-bring-back-list-personal.json").response.body as RemovedList;
const teamList = capture("113-bring-back-list-team-x.json").response.body as RemovedList;
const forbidden = capture("114-bring-back-list-pat-x-403.json").response;
const teamBack = capture("120-bring-back-team.json").response.body as BroughtBack;
const gone = capture("122-bring-back-team-again-404.json").response;
const withTeamCard = capture("127-bring-back-personal-page-team-card.json").response.body as SeriesPage;
const withoutTeamCard = capture("108-bring-back-personal-page-no-team-card.json").response.body as SeriesPage;
const teamPage = capture("128-bring-back-team-page-no-hint.json").response.body as SeriesPage;

beforeEach(() => {
  cleanup();
  Object.values(mocks).forEach((fn) => fn.mockReset());
});

describe("the captures are what these tests think they are", () => {
  it("has the list's fields, the 403's words and the hint's field", () => {
    expect(Object.keys(personalList.removed[0]).sort()).toEqual(["id", "removed_at", "removed_by", "team", "title"]);
    expect(personalList.removed[0].team.is_personal).toBe(true);
    expect(teamList.removed[0].team.is_personal).toBe(false);
    expect(forbidden.status).toBe(403);
    expect(forbidden.body.error.code).toBe("team_forbidden");
    expect(Object.keys(teamBack)).toEqual(["team"]);
    expect(gone.status).toBe(404);
    expect(withTeamCard.team_card).toEqual({ id: 26, team: { id: 4, name: "Platform team", is_personal: false } });
    expect(withoutTeamCard.team_card).toBeNull();
    expect(teamPage.team_card).toBeNull();
  });
});

describe("the Settings section", () => {
  it("draws nothing while waiting, when nothing was removed and when the call fails", async () => {
    mocks.removed.mockResolvedValue({ removed: [] });
    const { container, rerender } = render(<SettingsRemovedRecurring />);
    await waitFor(() => expect(mocks.removed).toHaveBeenCalledTimes(1));
    expect(container.innerHTML).toBe("");
    mocks.removed.mockRejectedValue(new ApiError(403, "team_forbidden", forbidden.body.error.message));
    rerender(<SettingsRemovedRecurring key="again" />);
    await waitFor(() => expect(mocks.removed).toHaveBeenCalledTimes(2));
    expect(container.innerHTML).toBe("");
  });

  it("lists what was removed, newest first as the server sent it, each with who removed it", async () => {
    mocks.removed.mockResolvedValue(personalList);
    render(<SettingsRemovedRecurring />);
    const section = (await screen.findByRole("heading", { name: "Removed recurring meetings" })).closest("section")!;
    expect(within(section).getByText("Weekly planning")).toBeTruthy();
    expect(within(section).getByText("Standup")).toBeTruthy();
    expect(within(section).getAllByText(/by Ada Example/)).toHaveLength(2);
    expect(within(section).getAllByRole("button", { name: /^Bring back / })).toHaveLength(2);
  });

  it("brings one back, drops its row and says when a Personal card returns", async () => {
    mocks.removed.mockResolvedValue(personalList);
    mocks.bringBack.mockResolvedValue({ team: personalList.removed[0].team });
    render(<SettingsRemovedRecurring />);
    await userEvent.click(await screen.findByRole("button", { name: "Bring back Weekly planning" }));
    expect(mocks.bringBack).toHaveBeenCalledWith(5); // the mark's id, not a series id
    const note = await screen.findByText(/is brought back/);
    expect(note.parentElement?.textContent).toContain("Your Personal space gets its card again at the next calendar sync");
    expect(note.parentElement?.textContent).toContain("Meetings you already recorded are not grouped into it.");
    expect(screen.queryByRole("button", { name: "Bring back Weekly planning" })).toBeNull();
    expect(screen.getByRole("button", { name: "Bring back Standup" })).toBeTruthy();
  });

  it("says that a team's card is made by an owner's or admin's share, not by the sync", async () => {
    mocks.removed.mockResolvedValue(teamList);
    mocks.bringBack.mockResolvedValue(teamBack);
    render(<SettingsRemovedRecurring />);
    await userEvent.click(await screen.findByRole("button", { name: "Bring back Recurring meeting" }));
    const note = await screen.findByText(/is brought back/);
    expect(note.parentElement?.textContent).toContain(
      "Platform team gets its card again the next time an owner or admin shares a meeting of it, or their recording of it is shared automatically.",
    );
  });

  it("treats a 404 as already brought back and shows another failure with the row kept", async () => {
    mocks.removed.mockResolvedValue(personalList);
    mocks.bringBack.mockRejectedValueOnce(new ApiError(404, "not_found", gone.body.error.message));
    render(<SettingsRemovedRecurring />);
    await userEvent.click(await screen.findByRole("button", { name: "Bring back Weekly planning" }));
    expect(await screen.findByText(/is brought back/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Bring back Weekly planning" })).toBeNull();
    mocks.bringBack.mockRejectedValueOnce(new ApiError(403, "team_forbidden", forbidden.body.error.message));
    await userEvent.click(screen.getByRole("button", { name: "Bring back Standup" }));
    expect(await screen.findByText(forbidden.body.error.message)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Bring back Standup" })).toBeTruthy();
  });

  it("allows one bring-back at a time", async () => {
    mocks.removed.mockResolvedValue(personalList);
    mocks.bringBack.mockReturnValue(new Promise(() => {}));
    render(<SettingsRemovedRecurring />);
    await userEvent.click(await screen.findByRole("button", { name: "Bring back Weekly planning" }));
    expect((screen.getByRole("button", { name: "Bring back Standup" }) as HTMLButtonElement).disabled).toBe(true);
    expect(mocks.bringBack).toHaveBeenCalledTimes(1);
  });
});

describe("the hint on a Personal Series page", () => {
  const hint = () => screen.queryByText(/This recurring meeting also has a card in/);

  it("names the team's card, says what that means for the lines typed here, and links to it", () => {
    render(<SeriesView initial={withTeamCard} multiTeam />);
    const note = hint()!;
    expect(note.textContent).toBe(
      "This recurring meeting also has a card in Platform team. Meetings you share with Platform team go to that card, so items you add here are not used for them. Add them to Platform team's card instead.",
    );
    const link = within(note).getByRole("link", { name: "Platform team's card" });
    expect(link.getAttribute("href")).toBe("/series/26");
  });

  it("is not drawn when no team card exists, nor on a team's own page", () => {
    render(<SeriesView initial={withoutTeamCard} multiTeam />);
    expect(hint()).toBeNull();
    cleanup();
    render(<SeriesView initial={teamPage} multiTeam />);
    expect(hint()).toBeNull();
  });
});
