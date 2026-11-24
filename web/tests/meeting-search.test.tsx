// @vitest-environment jsdom
import type React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingList } from "@/components/app/meeting-list";
import { TimeZoneProvider } from "@/components/app/time-zone-context";
import { ApiError } from "@/lib/api/client";
import { addSearch, cleanSearch, readSearch, searchHeading, searchProblem } from "@/lib/meeting-search";
import type { MeetingList as MeetingListData, Template } from "@/lib/api/types";
import { captured, formatsCapture } from "./helpers";

// The keyword search above the Meetings list (lead-designed): the box, the address, the heading, the empty
// state and "Show older meetings". The lists are the bodies captured from the running API
// (docs/fe-captures/export-formats-and-search/, `scripts/capture_export_formats_and_search.py`).

const mocks = vi.hoisted(() => ({ list: vi.fn(), refresh: vi.fn(), push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push, replace: mocks.replace, refresh: mocks.refresh }) }));
vi.mock("@/lib/api/browser", () => ({ api: { meetings: { list: mocks.list } } }));

const templates = captured<{ templates: Template[] }>("11-templates-seeded.json").templates;
const found = formatsCapture("23-meetings-q-notes.json").response.body as MeetingListData;
const page1 = formatsCapture("28-meetings-q-page-1.json").response.body as MeetingListData;
const page2 = formatsCapture("29-meetings-q-page-2.json").response.body as MeetingListData;
const nothing = formatsCapture("27-meetings-q-no-match.json").response.body as MeetingListData;
const view = (ui: React.ReactElement) => render(<TimeZoneProvider value="Africa/Lagos">{ui}</TimeZoneProvider>);
const form = () => screen.getByRole("search", { name: "Search meetings" });

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
  window.history.replaceState(null, "", "/");
});
afterEach(cleanup);

describe("the search text", () => {
  it("is trimmed and its spaces squeezed, as the server reads it", () => {
    expect(cleanSearch("  budget   review ")).toBe("budget review");
  });

  it("needs 2 to 100 characters, and empty is no search at all", () => {
    expect(searchProblem("")).toBeNull();
    expect(searchProblem("   ")).toBeNull();
    expect(searchProblem("a")).toBe("Type at least 2 characters to search.");
    expect(searchProblem("ab")).toBeNull();
    expect(searchProblem("x".repeat(100))).toBeNull();
    expect(searchProblem("x".repeat(101))).toBe("Search can be at most 100 characters.");
  });

  it("is read from the address, and a damaged value is dropped with a line about it", () => {
    expect(readSearch({ q: "  pricing  " })).toEqual({ q: "pricing", notice: null });
    expect(readSearch({ q: ["first", "second"] })).toEqual({ q: "first", notice: null });
    expect(readSearch({})).toEqual({ q: null, notice: null });
    expect(readSearch({ q: "" })).toEqual({ q: null, notice: null });
    expect(readSearch({ q: "a" })).toEqual({
      q: null,
      notice: "The search in the address could not be used (Type at least 2 characters to search), so every meeting is shown.",
    });
  });

  it("is added to an address beside the dates, and left off when there is none", () => {
    expect(addSearch("", null)).toBe("");
    expect(addSearch("", "pricing")).toBe("?q=pricing");
    expect(addSearch("?from=2026-11-01&to=2026-11-07", "a & b")).toBe("?from=2026-11-01&to=2026-11-07&q=a+%26+b");
  });

  it("has its own heading, and keeps the date range's words when there is one", () => {
    expect(searchHeading({ from: null, to: null }, "2026-11-14", "pricing")).toBe('Meetings matching "pricing"');
    expect(searchHeading({ from: "2026-11-09", to: "2026-11-09" }, "2026-11-14", "pricing")).toBe('Meetings on Mon 9 Nov matching "pricing"');
  });
});

describe("the search box on the Meetings list", () => {
  it("is drawn above the date filter with the search text in it, and Clear search while it is set", () => {
    view(<MeetingList initial={found} templates={templates} q="pricing" />);
    const box = within(form()).getByLabelText("Search meetings") as HTMLInputElement;
    expect(box.value).toBe("pricing");
    expect(within(form()).getByRole("button", { name: "Search" })).toBeTruthy();
    expect(within(form()).getByRole("button", { name: "Clear search" })).toBeTruthy();
    expect(screen.getByRole("form", { name: "Filter meetings by date" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe('Meetings matching "pricing"');
    expect(screen.getAllByRole("link").map((a) => a.textContent).join(" ")).toContain("Weekly pricing sync");
  });

  it("has no Clear search while the box is empty and no search is on", () => {
    view(<MeetingList initial={found} templates={templates} />);
    expect(within(form()).queryByRole("button", { name: "Clear search" })).toBeNull();
    expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Recent");
  });

  it("searches with Enter or the button by changing the address", async () => {
    view(<MeetingList initial={found} templates={templates} />);
    await userEvent.type(within(form()).getByLabelText("Search meetings"), "  budget   review {Enter}");
    expect(mocks.push).toHaveBeenLastCalledWith("/?q=budget+review", { scroll: false });
    await userEvent.click(within(form()).getByRole("button", { name: "Search" }));
    expect(mocks.push).toHaveBeenCalledTimes(2);
  });

  it("keeps the dates and the team in the address, and the search when the dates change", async () => {
    window.history.replaceState(null, "", "/?team=4");
    view(<MeetingList initial={found} templates={templates} range={{ from: "2026-11-01", to: "2026-11-07" }} q="pricing" />);
    await userEvent.clear(within(form()).getByLabelText("Search meetings"));
    await userEvent.type(within(form()).getByLabelText("Search meetings"), "roadmap{Enter}");
    expect(mocks.push).toHaveBeenLastCalledWith("/?from=2026-11-01&to=2026-11-07&q=roadmap&team=4", { scroll: false });
    await userEvent.click(screen.getByRole("button", { name: "Last 7 days" }));
    expect(mocks.push.mock.lastCall?.[0]).toMatch(/&q=pricing&team=4$|q=pricing/);
  });

  it("refuses one character in plain words and does not change the address", async () => {
    view(<MeetingList initial={found} templates={templates} />);
    await userEvent.type(within(form()).getByLabelText("Search meetings"), "a{Enter}");
    expect(within(form()).getByText("Type at least 2 characters to search.")).toBeTruthy();
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it("clears the search, keeping the dates", async () => {
    view(<MeetingList initial={found} templates={templates} range={{ from: "2026-11-01", to: null }} q="pricing" />);
    await userEvent.click(within(form()).getByRole("button", { name: "Clear search" }));
    expect(mocks.push).toHaveBeenLastCalledWith("/?from=2026-11-01", { scroll: false });
  });

  it("says plainly that nothing matches, and keeps the box so the words can be changed", () => {
    view(<MeetingList initial={nothing} templates={templates} q="zebra" />);
    expect(screen.getByText("No meetings match.")).toBeTruthy();
    expect(screen.getByText(/Try fewer or different words/)).toBeTruthy();
    expect((within(form()).getByLabelText("Search meetings") as HTMLInputElement).value).toBe("zebra");
    expect(screen.queryByText("No meetings yet.")).toBeNull();
  });

  it("asks for older meetings with the same search", async () => {
    mocks.list.mockResolvedValue(page2);
    view(<MeetingList initial={page1} templates={templates} q="pricing" />);
    await userEvent.click(screen.getByRole("button", { name: "Show older meetings" }));
    expect(mocks.list).toHaveBeenCalledWith({ before_id: page1.next_before_id, from: undefined, to: undefined, tz: undefined, q: "pricing" });
    expect((await screen.findAllByRole("link")).length).toBe(page1.meetings.length + page2.meetings.length);
  });

  it("starts the older pages again when the search changes", async () => {
    mocks.list.mockResolvedValue(page2);
    const { rerender } = view(<MeetingList initial={page1} templates={templates} q="pricing" />);
    await userEvent.click(screen.getByRole("button", { name: "Show older meetings" }));
    await screen.findAllByRole("link");
    rerender(
      <TimeZoneProvider value="Africa/Lagos">
        <MeetingList initial={page1} templates={templates} q="pricing review" />
      </TimeZoneProvider>,
    );
    expect(screen.getAllByRole("link").length).toBe(page1.meetings.length);
  });

  it("says so when older meetings could not be loaded", async () => {
    mocks.list.mockRejectedValue(new ApiError(500, "server_error", "x"));
    view(<MeetingList initial={page1} templates={templates} q="pricing" />);
    await userEvent.click(screen.getByRole("button", { name: "Show older meetings" }));
    expect(await screen.findByText("Could not load older meetings. Try again.")).toBeTruthy();
  });
});
