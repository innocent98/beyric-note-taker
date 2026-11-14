// @vitest-environment jsdom
import type React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "@/app/page";
import { MeetingList } from "@/components/app/meeting-list";
import { TimeZoneProvider } from "@/components/app/time-zone-context";
import type { MeetingList as MeetingListData, MeetingSummary, Template } from "@/lib/api/types";
import { captured } from "./helpers";

// The date filter on the Meetings list (lead-designed): the box, the quick ranges, the heading, the
// empty state, "Show older meetings" and the refresh on a filtered list, and the first page the
// server renders from the address.

const mocks = vi.hoisted(() => ({ list: vi.fn(), refresh: vi.fn(), push: vi.fn(), serverList: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  redirect: (to: string) => {
    throw new Error(`redirect:${to}`);
  },
  notFound: () => {
    throw new Error("not-found");
  },
  useRouter: () => ({ push: mocks.push, replace: vi.fn(), refresh: mocks.refresh }),
}));
vi.mock("@/lib/api/browser", () => ({ api: { meetings: { list: mocks.list } } }));
vi.mock("@/components/app/recurring-cards", () => ({ RecurringCards: () => null }));
vi.mock("@/lib/api/server", () => {
  const serverApi = async () => ({
    meetings: { list: mocks.serverList },
    templates: { list: async () => [{ id: 1, name: "General", instructions: "x", is_default: true }] },
    calendar: { get: async () => Promise.reject(new Error("off")), notRecorded: async () => ({ items: [] }) },
  });
  return {
    getSession: async () => ({ authenticated: true }),
    getReviewOpen: async () => null,
    getTasksNew: async () => 0,
    getUnreadNotifications: async () => null,
    getShellUser: async () => null,
    getTimeZone: async () => null,
    load: <T,>(call: Promise<T>) => call,
    serverApi,
    };
});

const templates = captured<{ templates: Template[] }>("11-templates-seeded.json").templates;
const base = captured<MeetingListData>("53-meetings-list.json").meetings[0];
// Sat 14 Nov 2026, 15:00 UTC (16:00 in Lagos).
const NOW = new Date("2026-11-14T15:00:00Z");

const meeting = (id: number, created_at: string, status: MeetingSummary["status"] = "ready"): MeetingSummary => ({
  ...base,
  id,
  created_at,
  title: `Meeting ${id}`,
  status,
  status_text: status === "ready" ? "Ready" : "Bot sent",
  status_tone: status === "ready" ? "success" : "warning",
});
const page = (meetings: MeetingSummary[], more = { has_more: false, next_before_id: null as number | null }, refresh: number | null = null): MeetingListData => ({
  ...captured<MeetingListData>("53-meetings-list.json"),
  meetings,
  refresh_after_seconds: refresh,
  ...more,
});
const headings = () => screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
const range = { from: "2026-11-09", to: "2026-11-12" };

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
  vi.useFakeTimers({ toFake: ["Date"], now: NOW });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("the filter box", () => {
  const first = page([meeting(3, "2026-11-12T10:00:00Z"), meeting(2, "2026-11-09T09:00:00Z")]);

  it("offers From and To date boxes, the three quick ranges, Apply, and Clear only while something is set", () => {
    render(<MeetingList initial={first} templates={templates} />);
    const form = screen.getByRole("form", { name: "Filter meetings by date" });
    const from = within(form).getByLabelText("From") as HTMLInputElement;
    expect(from.type).toBe("date");
    expect((within(form).getByLabelText("To") as HTMLInputElement).type).toBe("date");
    for (const name of ["Today", "Last 7 days", "Last 30 days", "Apply"]) expect(within(form).getByRole("button", { name })).toBeTruthy();
    expect(within(form).queryByRole("button", { name: "Clear" })).toBeNull();
  });

  it("is not drawn for an account with no meetings and no filter, so the empty page is unchanged", () => {
    render(<MeetingList initial={page([])} templates={templates} />);
    expect(screen.queryByRole("form", { name: "Filter meetings by date" })).toBeNull();
    expect(screen.getByText("No meetings yet.")).toBeTruthy();
  });

  it("applies the two dates with Apply, and with Enter in a box, by changing the address", async () => {
    render(<MeetingList initial={first} templates={templates} />);
    await userEvent.type(screen.getByLabelText("From"), "2026-11-01");
    await userEvent.type(screen.getByLabelText("To"), "2026-11-07{Enter}");
    expect(mocks.push).toHaveBeenLastCalledWith("/?from=2026-11-01&to=2026-11-07", { scroll: false });
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(mocks.push).toHaveBeenCalledTimes(2);
  });

  it("applies only one end: just From, or just To", async () => {
    render(<MeetingList initial={first} templates={templates} />);
    await userEvent.type(screen.getByLabelText("To"), "2026-11-07");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(mocks.push).toHaveBeenLastCalledWith("/?to=2026-11-07", { scroll: false });
  });

  it("refuses From after To in plain words and does not change the address", async () => {
    render(<MeetingList initial={first} templates={templates} />);
    await userEvent.type(screen.getByLabelText("From"), "2026-11-09");
    await userEvent.type(screen.getByLabelText("To"), "2026-11-07");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect((await screen.findByRole("alert")).textContent).toBe("From must be on or before To.");
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it("Today, Last 7 days and Last 30 days apply at once, in the owner's zone", async () => {
    // 23:30 UTC on Sat 14 Nov is 00:30 on Sun 15 Nov in Lagos: "today" there is the 15th.
    vi.setSystemTime(new Date("2026-11-14T23:30:00Z"));
    render(
      <TimeZoneProvider value="Africa/Lagos">
        <MeetingList initial={first} templates={templates} />
      </TimeZoneProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Today" }));
    expect(mocks.push).toHaveBeenLastCalledWith("/?from=2026-11-15&to=2026-11-15", { scroll: false });
    await userEvent.click(screen.getByRole("button", { name: "Last 7 days" }));
    expect(mocks.push).toHaveBeenLastCalledWith("/?from=2026-11-09&to=2026-11-15", { scroll: false });
    await userEvent.click(screen.getByRole("button", { name: "Last 30 days" }));
    expect(mocks.push).toHaveBeenLastCalledWith("/?from=2026-10-17&to=2026-11-15", { scroll: false });
    expect((screen.getByLabelText("From") as HTMLInputElement).value).toBe("2026-10-17");
  });

  it("changes the address of whichever page draws the list (basePath), so the filter can move with the page", async () => {
    render(<MeetingList initial={first} templates={templates} basePath="/somewhere" />);
    await userEvent.click(screen.getByRole("button", { name: "Today" }));
    expect(mocks.push).toHaveBeenLastCalledWith("/somewhere?from=2026-11-14&to=2026-11-14", { scroll: false });
  });

  it("starts with the range from the address, and Clear removes it", async () => {
    render(<MeetingList initial={first} templates={templates} range={range} />);
    expect((screen.getByLabelText("From") as HTMLInputElement).value).toBe("2026-11-09");
    expect((screen.getByLabelText("To") as HTMLInputElement).value).toBe("2026-11-12");
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(mocks.push).toHaveBeenLastCalledWith("/", { scroll: false });
  });

  it("can be used from the keyboard alone: every control is a labelled input or a button in tab order", async () => {
    render(<MeetingList initial={first} templates={templates} />);
    await userEvent.tab();
    expect(document.activeElement).toBe(screen.getByLabelText("From"));
    await userEvent.tab();
    expect(document.activeElement).toBe(screen.getByLabelText("To"));
    await userEvent.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Apply" }));
    await userEvent.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Today" }));
  });
});

describe("a filtered list", () => {
  const filtered = page([meeting(3, "2026-11-12T10:00:00Z"), meeting(2, "2026-11-09T09:00:00Z")]);

  it("shows the range in the heading, and the day headings keep working under it", () => {
    render(<MeetingList initial={filtered} templates={templates} range={range} />);
    expect(screen.getByRole("heading", { level: 2, name: "Meetings from Mon 9 Nov to Thu 12 Nov" })).toBeTruthy();
    expect(headings()).toEqual(["Thu 12 Nov", "Mon 9 Nov"]);
  });

  it("says No meetings in this range. when nothing matches, and keeps the filter to change", () => {
    render(<MeetingList initial={page([])} templates={templates} range={range} />);
    expect(screen.getByText("No meetings in this range.")).toBeTruthy();
    expect(screen.queryByText("No meetings yet.")).toBeNull();
    expect(screen.getByRole("heading", { level: 2, name: "Meetings from Mon 9 Nov to Thu 12 Nov" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Clear" })).toBeTruthy();
  });

  it("shows older meetings of the same range: the next page is asked for with the range and before_id", async () => {
    const one = page([meeting(10, "2026-11-12T18:00:00Z")], { has_more: true, next_before_id: 10 });
    mocks.list.mockResolvedValue(page([meeting(9, "2026-11-11T08:00:00Z")]));
    render(<MeetingList initial={one} templates={templates} range={range} />);
    await userEvent.click(screen.getByRole("button", { name: "Show older meetings" }));
    expect(mocks.list).toHaveBeenCalledWith({ before_id: 10, from: "2026-11-09", to: "2026-11-12" });
    expect(await screen.findByText("Meeting 9")).toBeTruthy();
    expect(headings()).toEqual(["Thu 12 Nov", "Wed 11 Nov"]);
  });

  it("asks without from and to when there is no filter, as before", async () => {
    mocks.list.mockResolvedValue(page([meeting(9, "2026-11-11T08:00:00Z")]));
    render(<MeetingList initial={page([meeting(10, "2026-11-12T18:00:00Z")], { has_more: true, next_before_id: 10 })} templates={templates} />);
    await userEvent.click(screen.getByRole("button", { name: "Show older meetings" }));
    expect(mocks.list).toHaveBeenCalledWith({ before_id: 10, from: undefined, to: undefined });
  });

  it("forgets older pages when the range changes, and starts again from the server's first page", async () => {
    const one = page([meeting(10, "2026-11-12T18:00:00Z")], { has_more: true, next_before_id: 10 });
    mocks.list.mockResolvedValue(page([meeting(9, "2026-11-11T08:00:00Z")]));
    const { rerender } = render(<MeetingList initial={one} templates={templates} range={range} />);
    await userEvent.click(screen.getByRole("button", { name: "Show older meetings" }));
    expect(await screen.findByText("Meeting 9")).toBeTruthy();
    rerender(<MeetingList initial={page([meeting(20, "2026-11-13T10:00:00Z")])} templates={templates} range={{ from: "2026-11-13", to: "2026-11-13" }} />);
    expect(screen.queryByText("Meeting 9")).toBeNull();
    expect(screen.getByText("Meeting 20")).toBeTruthy();
  });

  it("keeps the filter when the list refreshes itself: the refresh re-renders the same address", () => {
    vi.useRealTimers();
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"], now: NOW });
    const working = page([meeting(3, "2026-11-12T10:00:00Z", "bot_sent")], { has_more: false, next_before_id: null }, 30);
    render(<MeetingList initial={working} templates={templates} range={range} />);
    vi.advanceTimersByTime(30_000);
    expect(mocks.refresh).toHaveBeenCalled();
    expect(mocks.push).not.toHaveBeenCalled(); // no navigation: the address, and so the range, is untouched
    expect((screen.getByLabelText("From") as HTMLInputElement).value).toBe("2026-11-09");
  });

  it("says why a damaged address was ignored", () => {
    render(<MeetingList initial={filtered} templates={templates} rangeNotice="The dates in the address could not be used (From is not a date), so every meeting is shown." />);
    expect(screen.getByText(/could not be used/)).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Recent" })).toBeTruthy();
  });
});

describe("the page the server renders", () => {
  // `HomePage` returns the async `MeetingsPage` element for a signed-in owner; run that one too.
  const render1 = async (params: Record<string, string>) => {
    const element = (await HomePage({ searchParams: Promise.resolve(params) })) as {
      type: (props: unknown) => Promise<React.ReactElement>;
      props: unknown;
    };
    return renderToStaticMarkup(await element.type(element.props));
  };

  it("asks the API for the range in the address and draws the filter with it", async () => {
    mocks.serverList.mockResolvedValue(page([meeting(3, "2026-11-12T10:00:00Z")]));
    const html = await render1(range);
    expect(mocks.serverList).toHaveBeenCalledWith({ from: "2026-11-09", to: "2026-11-12" });
    expect(html).toContain("Meetings from Mon 9 Nov to Thu 12 Nov");
    expect(html).toContain('value="2026-11-09"');
    expect(html).toContain('value="2026-11-12"');
  });

  it("asks for no range without one, once, exactly as before", async () => {
    mocks.serverList.mockResolvedValue(page([meeting(3, "2026-11-12T10:00:00Z")]));
    await render1({});
    expect(mocks.serverList).toHaveBeenCalledTimes(1);
    expect(mocks.serverList).toHaveBeenCalledWith({ from: undefined, to: undefined });
  });

  it("ignores a damaged address, lists everything, and says so", async () => {
    mocks.serverList.mockResolvedValue(page([meeting(3, "2026-11-12T10:00:00Z")]));
    const html = await render1({ from: "not-a-date" });
    expect(mocks.serverList).toHaveBeenCalledWith({ from: undefined, to: undefined });
    expect(html).toContain("could not be used");
  });

  it("decides whether a bot is on a meeting from the newest meetings, not the filtered ones", async () => {
    mocks.serverList.mockImplementation(async (query: { limit?: number; from?: string }) =>
      query.limit === 10 ? page([meeting(9, "2026-11-14T10:00:00Z", "bot_sent")]) : page([meeting(3, "2026-11-12T10:00:00Z")]),
    );
    const html = await render1(range);
    expect(html).toContain("A bot is already on a meeting. Wait for it to finish first.");
  });
});
