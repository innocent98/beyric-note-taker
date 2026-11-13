// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingList } from "@/components/app/meeting-list";
import { TimeZoneProvider } from "@/components/app/time-zone-context";
import type { MeetingList as MeetingListData, MeetingSummary, Template } from "@/lib/api/types";
import { captured } from "./helpers";

const mocks = vi.hoisted(() => ({ list: vi.fn(), refresh: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: mocks.refresh }),
}));
vi.mock("@/lib/api/browser", () => ({ api: { meetings: { list: mocks.list } } }));

const templates = captured<{ templates: Template[] }>("11-templates-seeded.json").templates;
const base = captured<MeetingListData>("53-meetings-list.json").meetings[0];

// Fri 13 Nov 2026, 15:00 UTC (16:00 in Lagos).
const NOW = new Date("2026-11-13T15:00:00Z");

const meeting = (id: number, created_at: string): MeetingSummary => ({
  ...base,
  id,
  created_at,
  title: `Meeting ${id}`,
  status: "ready",
  status_text: "Ready",
  status_tone: "success",
});
const page = (meetings: MeetingSummary[], more: { has_more: boolean; next_before_id: number | null }): MeetingListData => ({
  ...captured<MeetingListData>("53-meetings-list.json"),
  meetings,
  refresh_after_seconds: null,
  ...more,
});
const headings = () => screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
const hrefs = () => screen.getAllByRole("link").map((a) => a.getAttribute("href"));

beforeEach(() => {
  mocks.list.mockReset();
  mocks.refresh.mockReset();
  vi.useFakeTimers({ toFake: ["Date"], now: NOW });
});
afterEach(() => vi.useRealTimers());

describe("meeting list day headings", () => {
  const first = page(
    [meeting(5, "2026-11-13T10:00:00Z"), meeting(4, "2026-11-13T08:00:00Z"), meeting(3, "2026-11-12T09:00:00Z"), meeting(2, "2026-11-09T09:00:00Z")],
    { has_more: false, next_before_id: null },
  );

  it("puts a heading above each day and each day's meetings in a list that the heading names", () => {
    render(<MeetingList initial={first} templates={templates} />);
    expect(headings()).toEqual(["Today", "Yesterday", "Mon 9 Nov"]);
    expect(within(screen.getByRole("list", { name: "Today" })).getAllByRole("listitem")).toHaveLength(2);
    expect(within(screen.getByRole("list", { name: "Yesterday" })).getByText("Meeting 3")).toBeTruthy();
    expect(within(screen.getByRole("list", { name: "Mon 9 Nov" })).getByText("Meeting 2")).toBeTruthy();
  });

  it("leaves the order of the links, and so the keyboard order, as the API gave it", () => {
    render(<MeetingList initial={first} templates={templates} />);
    expect(hrefs()).toEqual(["/meetings/5", "/meetings/4", "/meetings/3", "/meetings/2"]);
  });

  it("shows the year only for a day that is not this year", () => {
    const old = page([meeting(2, "2026-11-13T10:00:00Z"), meeting(1, "2025-12-30T10:00:00Z")], { has_more: false, next_before_id: null });
    render(<MeetingList initial={old} templates={templates} />);
    expect(headings()).toEqual(["Today", "Tue 30 Dec 2025"]);
  });

  it("uses the owner's stored zone for the headings, the same zone the rows' own times are written in", () => {
    // 23:30 UTC on Thu 12 Nov is 00:30 on Fri 13 Nov in Lagos: Today there, Yesterday in UTC.
    const late = page([meeting(1, "2026-11-12T23:30:00Z")], { has_more: false, next_before_id: null });
    const { unmount } = render(
      <TimeZoneProvider value="Africa/Lagos">
        <MeetingList initial={late} templates={templates} />
      </TimeZoneProvider>,
    );
    expect(headings()).toEqual(["Today"]);
    expect(screen.getByText("13 Nov 2026, 00:30 GMT+1")).toBeTruthy();
    unmount();
    render(
      <TimeZoneProvider value="UTC">
        <MeetingList initial={late} templates={templates} />
      </TimeZoneProvider>,
    );
    expect(headings()).toEqual(["Yesterday"]);
    expect(screen.getByText("12 Nov 2026, 23:30 UTC")).toBeTruthy();
  });

  it("splits at midnight in the owner's zone: two meetings a second apart get two headings in Lagos", () => {
    const edge = page([meeting(2, "2026-11-11T23:00:00Z"), meeting(1, "2026-11-11T22:59:59Z")], { has_more: false, next_before_id: null });
    render(
      <TimeZoneProvider value="Africa/Lagos">
        <MeetingList initial={edge} templates={templates} />
      </TimeZoneProvider>,
    );
    expect(headings()).toEqual(["Yesterday", "Wed 11 Nov"]); // 00:00 Thu 12 Nov, and 23:59:59 Wed 11 Nov, in Lagos
  });

  it("continues a day under the same heading when Show older meetings returns more of it", async () => {
    const one = page([meeting(10, "2026-11-12T18:00:00Z"), meeting(9, "2026-11-12T12:00:00Z")], { has_more: true, next_before_id: 9 });
    mocks.list.mockResolvedValue({
      ...one,
      meetings: [meeting(8, "2026-11-12T08:00:00Z"), meeting(7, "2026-11-11T20:00:00Z")],
      has_more: false,
      next_before_id: null,
    });
    render(<MeetingList initial={one} templates={templates} />);
    expect(headings()).toEqual(["Yesterday"]);
    await userEvent.click(screen.getByRole("button", { name: "Show older meetings" }));
    expect(await screen.findByText("Meeting 7")).toBeTruthy();
    expect(headings()).toEqual(["Yesterday", "Wed 11 Nov"]);
    expect(within(screen.getByRole("list", { name: "Yesterday" })).getAllByRole("listitem")).toHaveLength(3);
    expect(hrefs()).toEqual(["/meetings/10", "/meetings/9", "/meetings/8", "/meetings/7"]);
  });

  it("keeps the older pages' headings where they are when the first page is refreshed with a new meeting", async () => {
    const one = page([meeting(10, "2026-11-12T18:00:00Z"), meeting(9, "2026-11-12T12:00:00Z")], { has_more: true, next_before_id: 9 });
    mocks.list.mockResolvedValue({
      ...one,
      meetings: [meeting(8, "2026-11-12T08:00:00Z"), meeting(7, "2026-11-11T20:00:00Z")],
      has_more: false,
      next_before_id: null,
    });
    const { rerender } = render(<MeetingList initial={one} templates={templates} />);
    await userEvent.click(screen.getByRole("button", { name: "Show older meetings" }));
    await screen.findByText("Meeting 7");
    const before = headings();
    // The live refresh: the server renders a new first page (a meeting of today on top).
    const refreshed = { ...one, meetings: [meeting(11, "2026-11-13T14:00:00Z"), ...one.meetings] };
    rerender(<MeetingList initial={refreshed} templates={templates} />);
    expect(headings()).toEqual(["Today", ...before]);
    expect(new Set(headings()).size).toBe(headings().length);
    expect(hrefs()).toEqual(["/meetings/11", "/meetings/10", "/meetings/9", "/meetings/8", "/meetings/7"]);
  });

  it("says nothing of days when there are no meetings", () => {
    render(<MeetingList initial={page([], { has_more: false, next_before_id: null })} templates={templates} />);
    expect(screen.queryAllByRole("heading", { level: 3 })).toHaveLength(0);
    expect(screen.getByText("No meetings yet.")).toBeTruthy();
  });
});
