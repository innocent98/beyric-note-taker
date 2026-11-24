// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingView } from "@/components/app/meeting-view";
import type { Brief, Meeting, RecipeList, Template } from "@/lib/api/types";
import { captured, recipesLibraryBody } from "./helpers";

// The Make button of the meeting page (lead-designed: the designer's files draw nothing of recipes). It sits
// with the other header actions of a ready meeting, for anyone who can read the meeting, and opens the dialog
// (tests/make-dialog.test.tsx covers the dialog itself).
const mocks = vi.hoisted(() => ({ list: vi.fn(), brief: vi.fn(), teams: vi.fn(), make: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    milestones: { forMeeting: () => Promise.resolve({ milestones: [] }), confirm: vi.fn() },
    series: { list: vi.fn().mockResolvedValue({ timezone: "UTC", series: [] }), create: vi.fn() },
    meetings: { get: vi.fn() },
    recipes: { list: mocks.list },
    brief: { get: mocks.brief },
    teams: { list: mocks.teams },
    documents: { make: mocks.make },
  },
}));

const templates: Template[] = [{ id: 1, name: "General", instructions: "g" }];
const ready = () => captured<Meeting>("47-meeting-detail-ready.json");

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
  mocks.list.mockResolvedValue(recipesLibraryBody<RecipeList>("11-list-starters.json"));
  mocks.brief.mockResolvedValue(recipesLibraryBody<Brief>("39-brief-put.json"));
  mocks.teams.mockResolvedValue({ teams: [], can_create_more: 0 });
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
    this.removeAttribute("open");
  };
});
afterEach(cleanup);

describe("the Make button", () => {
  it("is drawn on a ready meeting with notes, beside Share notes", () => {
    render(<MeetingView initial={{ ...ready(), can_share: true }} templates={templates} />);
    expect(screen.getByRole("button", { name: /Make/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Share notes/ })).toBeTruthy();
  });

  it("is drawn for a teammate who cannot share the meeting but can read it", () => {
    render(<MeetingView initial={{ ...ready(), can_share: false }} templates={templates} />);
    expect(screen.getByRole("button", { name: /Make/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Share notes/ })).toBeNull();
  });

  it("is not drawn while the notes are still being written, or on a failed meeting", () => {
    for (const file of ["43-meeting-detail-processing.json", "49-meeting-detail-failed-retryable.json", "35-meeting-detail-bot-sent.json"]) {
      const { unmount } = render(<MeetingView initial={captured<Meeting>(file)} templates={templates} />);
      expect(screen.queryByRole("button", { name: /^Make/ })).toBeNull();
      unmount();
    }
  });

  it("opens the dialog for this meeting, and asks for nothing until it is open", async () => {
    const m = ready();
    render(<MeetingView initial={m} templates={templates} />);
    expect(mocks.list).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /Make/ }));
    expect(await screen.findByRole("heading", { name: "Make from this meeting" })).toBeTruthy();
    expect(screen.getByText(`From ${m.title}.`)).toBeTruthy();
    await screen.findByRole("radio", { name: /Follow-up email/ });
    await userEvent.click(screen.getByRole("button", { name: "Make draft" }));
    await waitFor(() => expect(mocks.make).toHaveBeenCalled());
    expect(mocks.make.mock.calls[0][0].meeting_ids).toEqual([m.id]);
  });
});
