// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { join } from "node:path";
import { RecurringCards } from "@/components/app/recurring-cards";
import { SeriesView } from "@/components/app/series-view";
import { ApiError } from "@/lib/api/client";
import type { SeriesList, SeriesPage } from "@/lib/api/types";
import { SERIES_REMOVED_FLAG, SERIES_REMOVED_MESSAGE } from "@/lib/deleted-flag";
import { REPO, read } from "./helpers";

// "Remove this recurring meeting" (LEAD-DESIGNED, 2026-11-15; docs/fe-integration-guide-series-teams.md
// section 10). Every page and answer is one captured from the running app
// (docs/fe-captures/series-follow/52 to 76, made by scripts/capture_series_follow.py).

const mocks = vi.hoisted(() => ({ remove: vi.fn(), list: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push, refresh: vi.fn() }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    series: { remove: mocks.remove, list: mocks.list, get: vi.fn(), addAgenda: vi.fn(), removeAgenda: vi.fn(), sendAgenda: vi.fn() },
    questions: { update: vi.fn() },
    tasks: { list: vi.fn().mockResolvedValue({ people: [] }) },
  },
}));

const capture = (file: string) => JSON.parse(read(join(REPO, "docs/fe-captures/series-follow", file)));
const owned = capture("52-remove-page-ada-x-before.json").response.body as SeriesPage;
const notAllowed = capture("53-remove-page-pat-x-cannot.json").response.body as SeriesPage;
const personalPage = capture("25-page-ada-personal.json").response.body as SeriesPage;
const forbidden = capture("54-remove-pat-x-403.json").response;
const removed = capture("64-remove-ada-x-standup.json").response.body;
const emptiedPersonalRemoved = capture("74-remove-ada-personal-standup-emptied.json").response.body;
const personalCards = capture("59-remove-cards-ada-personal-1on1-kept.json").response.body as SeriesList;

beforeEach(() => {
  cleanup();
  Object.values(mocks).forEach((fn) => fn.mockReset());
  sessionStorage.clear();
});

const ask = async (page: SeriesPage, multiTeam = true) => {
  render(<SeriesView initial={page} multiTeam={multiTeam} />);
  await userEvent.click(screen.getByRole("button", { name: "Remove this recurring meeting" }));
  return screen.getByRole("group", { name: "Remove this recurring meeting?" });
};

describe("the capture is what these tests think it is", () => {
  it("says who may remove, what the answer holds and the 403's words", () => {
    expect(owned.can_remove).toBe(true);
    expect(notAllowed.can_remove).toBe(false);
    expect(removed).toEqual({ meetings_kept: 1, agenda_lines_removed: 0, remembered: true });
    expect(emptiedPersonalRemoved.agenda_lines_removed).toBe(1);
    expect(forbidden.status).toBe(403);
    expect(forbidden.body.error.code).toBe("team_forbidden");
  });
});

describe("the button", () => {
  it("is drawn when the server says the caller may remove it", () => {
    render(<SeriesView initial={owned} multiTeam />);
    expect(screen.getByRole("button", { name: "Remove this recurring meeting" })).toBeTruthy();
  });

  it("is not drawn when it says the caller may not", () => {
    render(<SeriesView initial={notAllowed} multiTeam />);
    expect(screen.queryByRole("button", { name: "Remove this recurring meeting" })).toBeNull();
  });

  it("is not drawn on a body from before the field existed", () => {
    const older: Partial<SeriesPage> = { ...owned };
    delete older.can_remove;
    render(<SeriesView initial={older as SeriesPage} multiTeam />);
    expect(screen.queryByRole("button", { name: "Remove this recurring meeting" })).toBeNull();
  });
});

describe("the question", () => {
  it("says the plain words and what happens to each thing", async () => {
    const group = await ask(owned);
    expect(within(group).getByText(/The past meetings stay\. They will not be grouped any more\. No meeting is deleted\./)).toBeTruthy();
    expect(within(group).getByText(/You can't undo this\./)).toBeTruthy();
    expect(within(group).queryByText(/Nothing is deleted/)).toBeNull();
    for (const label of ["Past meetings:", "Waiting agenda items:", "Linked project:", "Keeps coming up:", "Tasks and open questions:", "Your calendar:"]) {
      expect(within(group).getByText(label)).toBeTruthy();
    }
    expect(within(group).getByText(/none are waiting/)).toBeTruthy();
  });

  it("counts the waiting agenda lines that will go", async () => {
    const page = { ...owned, agenda: [{ id: 1, text: "Budget", can_remove: true }, { id: 2, text: "Hiring", can_remove: true }] };
    const group = await ask(page);
    expect(within(group).getByText(/2 lines will be removed\./)).toBeTruthy();
  });

  it("says a team row goes for everyone in the team, and a personal row only for the person", async () => {
    const group = await ask(owned);
    expect(within(group).getByText(/This removes it for everyone in Platform team\./)).toBeTruthy();
    cleanup();
    const personal = await ask({ ...personalPage, can_remove: true });
    expect(within(personal).queryByText(/for everyone in/)).toBeNull();
    expect(within(personal).getByText(/Your past meetings stay\./)).toBeTruthy();
    expect(within(personal).getByText(/You can't undo this\./)).toBeTruthy();
    expect(within(personal).getByText(/this removes it from your Personal space only\. The same recurring meeting on a team's page is not changed\./)).toBeTruthy();
    expect(within(personal).queryByText(/your personal page/)).toBeNull();
  });

  it("says it removes from this team only, while multi-team is on", async () => {
    const group = await ask(owned);
    expect(within(group).getByText(/removes it from Platform team only/)).toBeTruthy();
    cleanup();
    const solo = await ask(owned, false);
    expect(within(solo).queryByText("Other pages:")).toBeNull();
  });

  it("Cancel closes it and calls nothing", async () => {
    await ask(owned);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(mocks.remove).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Remove this recurring meeting" })).toBeTruthy();
  });
});

describe("the question for an empty row", () => {
  const empty = { ...owned, past_meetings: [], agenda: [] };

  it("says nothing is grouped under it, and keeps the lines that are still true", async () => {
    const group = await ask(empty);
    expect(within(group).getByText(/No meeting is grouped under it and nothing is waiting in it\./)).toBeTruthy();
    expect(within(group).getByText(/You can't undo this\./)).toBeTruthy();
    expect(within(group).getByText("Your calendar:")).toBeTruthy();
    expect(within(group).queryByText("Past meetings:")).toBeNull();
    expect(within(group).queryByText("Waiting agenda items:")).toBeNull();
    expect(within(group).queryByText(/for everyone in/)).toBeNull();
  });

  it("is the full question as soon as a line is waiting", async () => {
    const group = await ask({ ...empty, agenda: [{ id: 1, text: "Budget", can_remove: true }] });
    expect(within(group).getByText("Past meetings:")).toBeTruthy();
    expect(within(group).getByText(/1 line will be removed\./)).toBeTruthy();
  });
});

describe("removing", () => {
  it("calls the server for this row, remembers to say so, and goes to the list", async () => {
    mocks.remove.mockResolvedValue(removed);
    const group = await ask(owned);
    await userEvent.click(within(group).getByRole("button", { name: "Remove recurring meeting" }));
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/"));
    expect(mocks.remove).toHaveBeenCalledWith(owned.id);
    expect(sessionStorage.getItem(SERIES_REMOVED_FLAG)).toBe("1");
  });

  it("treats a 404 as already removed", async () => {
    mocks.remove.mockRejectedValue(new ApiError(404, "not_found", "No such recurring meeting"));
    const group = await ask(owned);
    await userEvent.click(within(group).getByRole("button", { name: "Remove recurring meeting" }));
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/"));
  });

  it("shows the server's words on a 403 and stays on the page", async () => {
    mocks.remove.mockRejectedValue(new ApiError(403, forbidden.body.error.code, forbidden.body.error.message));
    const group = await ask(owned);
    await userEvent.click(within(group).getByRole("button", { name: "Remove recurring meeting" }));
    expect(await screen.findByText(forbidden.body.error.message)).toBeTruthy();
    expect(mocks.push).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Remove this recurring meeting" })).toBeTruthy();
  });
});

describe("the Recurring cards after a removal", () => {
  it("say it once, even when no recurring meeting is left", async () => {
    sessionStorage.setItem(SERIES_REMOVED_FLAG, "1");
    mocks.list.mockResolvedValue({ timezone: "UTC", series: [] });
    render(<RecurringCards multiTeam />);
    expect(await screen.findByText(SERIES_REMOVED_MESSAGE)).toBeTruthy();
    expect(sessionStorage.getItem(SERIES_REMOVED_FLAG)).toBeNull();
  });

  it("say it above the cards that are left", async () => {
    sessionStorage.setItem(SERIES_REMOVED_FLAG, "1");
    mocks.list.mockResolvedValue(personalCards);
    render(<RecurringCards multiTeam />);
    expect(await screen.findByText(SERIES_REMOVED_MESSAGE)).toBeTruthy();
    expect(screen.getAllByRole("listitem").length).toBe(personalCards.series.length);
  });

  it("say nothing when nothing was removed", async () => {
    mocks.list.mockResolvedValue({ timezone: "UTC", series: [] });
    const { container } = render(<RecurringCards multiTeam />);
    await waitFor(() => expect(mocks.list).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });
});
