import type React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import HomePage from "@/app/page";
import type { CalendarState, MeetingList } from "@/lib/api/types";
import { calendarBody, captured } from "./helpers";

// Where the Recurring cards sit on the Meetings page: below the calendar's Upcoming list and above
// the list of meetings, as in the designer's file. The cards fetch for themselves in the browser,
// so the server page only places them (their own behaviour is in recurring-cards.test.tsx).

vi.mock("next/navigation", () => ({
  redirect: (to: string) => {
    throw new Error(`redirect:${to}`);
  },
  notFound: () => {
    throw new Error("not-found");
  },
  useRouter: () => ({ refresh: vi.fn() }),
}));
vi.mock("@/components/app/recurring-cards", () => ({
  RecurringCards: () => <div data-testid="recurring-cards" />,
}));
vi.mock("@/lib/api/server", () => ({
  getSession: async () => ({ authenticated: true }),
  getReviewOpen: async () => null,
  getUnreadNotifications: async () => null,
  load: <T,>(call: Promise<T>) => call,
  serverApi: async () => ({
    meetings: { list: async () => captured<MeetingList>("53-meetings-list.json") },
    templates: { list: async () => [{ id: 1, name: "General", instructions: "x", is_default: true }] },
    calendar: { get: async () => calendarBody<CalendarState>("09-state-connected-with-upcoming.json") },
  }),
}));

const render = async () => {
  const element = (await HomePage()) as { type: (props: unknown) => Promise<React.ReactElement>; props: unknown };
  return renderToStaticMarkup(await element.type(element.props));
};

describe("Meetings page", () => {
  it("places the Recurring cards between the Upcoming list and the list of meetings", async () => {
    const html = await render();
    const upcoming = html.indexOf('id="upcoming-title"');
    const recurring = html.indexOf('data-testid="recurring-cards"');
    const recent = html.indexOf("Recent");
    expect(upcoming).toBeGreaterThan(-1);
    expect(recurring).toBeGreaterThan(upcoming);
    expect(recent).toBeGreaterThan(recurring);
  });
});
