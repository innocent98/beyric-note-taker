import type React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "@/app/page";
import { ApiError } from "@/lib/api/client";
import type { CalendarState, MeetingList } from "@/lib/api/types";
import { calendarBody, captured } from "./helpers";

// The Meetings page with the calendar: the band while nothing is connected, the Upcoming list
// once it is, and the page as it was when the calendar is off or cannot be read.

const state = vi.hoisted(() => ({ calendar: null as null | (() => Promise<unknown>) }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  redirect: (to: string) => {
    throw new Error(`redirect:${to}`);
  },
  notFound: () => {
    throw new Error("not-found");
  },
  useRouter: () => ({ refresh: vi.fn() }),
}));
vi.mock("@/lib/api/server", () => ({
  getSession: async () => ({ authenticated: true }),
  getReviewOpen: async () => null,
  getTasksNew: async () => 0,
  getUnreadNotifications: async () => null,
  getShellUser: async () => null,
  getShellTeams: async () => null,
  getTimeZone: async () => null,
  load: <T,>(call: Promise<T>) => call,
  serverApi: async () => ({
    meetings: { list: async () => captured<MeetingList>("53-meetings-list.json") },
    templates: { list: async () => [{ id: 1, name: "General", instructions: "x", is_default: true }] },
    calendar: { get: () => state.calendar!() },
  }),
}));

const notConnected = calendarBody<CalendarState>("05-state-not-connected.json");
const withEvents = calendarBody<CalendarState>("09-state-connected-with-upcoming.json");

beforeEach(() => {
  state.calendar = async () => notConnected;
});

// `HomePage` returns the async `MeetingsPage` element; run that one too (static rendering cannot).
const render = async () => {
  const element = (await HomePage()) as { type: (props: unknown) => Promise<React.ReactElement>; props: unknown };
  return renderToStaticMarkup(await element.type(element.props));
};

describe("Meetings page calendar", () => {
  it("draws the Connect calendar band between the new-meeting form and the list when nothing is connected", async () => {
    const html = await render();
    expect(html).toContain("Join meetings automatically");
    expect(html).toContain('href="/settings"');
    expect(html.indexOf("New meeting")).toBeLessThan(html.indexOf("Join meetings automatically"));
  });

  it("draws Upcoming with the account and the rule when it is connected", async () => {
    state.calendar = async () => withEvents;
    const html = await render();
    expect(html).toContain("Upcoming");
    expect(html).toContain("grace@example.test");
    expect(html).toContain("Joining all meetings");
    expect(html).toContain("Weekly planning");
    expect(html).not.toContain("Join meetings automatically");
  });

  it("draws the page without any calendar part while the server has it off (404)", async () => {
    state.calendar = async () => {
      throw new ApiError(404, "calendar_unavailable", "Google Calendar is not available.");
    };
    const html = await render();
    expect(html).not.toContain("Join meetings automatically");
    expect(html).not.toContain("Upcoming");
    expect(html).toContain("New meeting");
  });

  it("does the same when the calendar read fails for any other reason", async () => {
    state.calendar = async () => {
      throw new ApiError(500, "internal_error", "Something went wrong.");
    };
    const html = await render();
    expect(html).not.toContain("Join meetings automatically");
    expect(html).toContain("New meeting");
  });
});
