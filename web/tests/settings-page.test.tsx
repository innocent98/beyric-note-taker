import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import SettingsPage, { metadata } from "@/app/settings/page";
import { AppShell } from "@/components/app/shell";
import { ApiError } from "@/lib/api/client";
import { APP_PATHS, NEXT_PAGES, isNextPage } from "@/lib/routes";
import type { CalendarState } from "@/lib/api/types";
import { calendarBody, read, WEB } from "./helpers";
import { join } from "node:path";

// The server half of /settings/calendar, and where it sits in the app's routes and sidebar.

const state = vi.hoisted(() => ({
  authenticated: true,
  answer: null as null | (() => Promise<unknown>),
  calls: 0,
}));
vi.mock("next/navigation", () => ({
  redirect: (to: string) => {
    throw new Error(`redirect:${to}`);
  },
  notFound: () => {
    throw new Error("not-found");
  },
  useRouter: () => ({ refresh: vi.fn() }),
}));
vi.mock("@/lib/api/server", () => ({
  requireOwner: async () => {
    if (!state.authenticated) throw new Error("redirect:/login");
    return {
      calendar: {
        get: async () => {
          state.calls += 1;
          return state.answer!();
        },
      },
    };
  },
  getReviewOpen: async () => 2,
}));

const connected = calendarBody<CalendarState>("09-state-connected-with-upcoming.json");
const render = async (params: Record<string, string | string[]> = {}) =>
  renderToStaticMarkup(await SettingsPage({ searchParams: Promise.resolve(params) }));

describe("route", () => {
  it("is a Next page the crawlers are told to leave alone", () => {
    expect(NEXT_PAGES).toContain("/settings");
    expect(NEXT_PAGES).not.toContain("/settings/calendar");
    expect(isNextPage("/settings")).toBe(true);
    expect(isNextPage("/settings?calendar=connected")).toBe(true);
    expect(APP_PATHS).toContain("/settings");
    expect(metadata.robots).toEqual({ index: false, follow: false });
  });
});

describe("sidebar", () => {
  it("has the designer's Settings entry after Templates and Vocabulary, current on its page", () => {
    const html = renderToStaticMarkup(<AppShell current="settings">x</AppShell>);
    expect(html).toMatch(/href="\/settings"[^>]*aria-current="page"|aria-current="page"[^>]*href="\/settings"/);
    expect(html.indexOf('href="/templates"')).toBeLessThan(html.indexOf('href="/vocabulary"'));
    expect(html.indexOf('href="/vocabulary"')).toBeLessThan(html.indexOf('href="/settings"'));
    const elsewhere = renderToStaticMarkup(<AppShell current="meetings">x</AppShell>);
    expect(elsewhere).toMatch(/href="\/settings"/);
    expect(elsewhere).not.toMatch(/href="\/settings"[^>]*aria-current/);
  });
});

describe("page", () => {
  it("draws the connected state from GET /api/calendar inside the app shell", async () => {
    state.answer = async () => connected;
    const html = await render();
    expect(html).toContain("Preferences for your notes and transcripts.");
    expect(html).toContain("grace@example.test");
    expect(html).toContain("Join automatically");
    expect(html).not.toContain("Weekly planning"); // the list is on the Meetings page
    expect(html).toContain(", 2 open");
  });

  it("draws the designer's other sections below Calendar, in order, whatever the calendar's state", async () => {
    const off = async () => {
      throw new ApiError(404, "calendar_unavailable", "Google Calendar is not available.");
    };
    for (const answer of [async () => connected, off]) {
      state.answer = answer;
      const html = await render();
      const order = ["Calendar", "Sprints and reminders", "Tasks", "Default notes language"].map((title) =>
        html.indexOf(`>${title}</h2>`),
      );
      expect(order.every((at) => at > -1)).toBe(true);
      expect([...order].sort((a, b) => a - b)).toEqual(order);
    }
  });

  it("shows the message for the callback's redirect", async () => {
    state.answer = async () => connected;
    expect(await render({ calendar: "connected" })).toContain("Google Calendar connected.");
    expect(await render({ calendar_error: "scope_missing" })).toContain("leave the calendar box ticked");
  });

  it("says the calendar is not available yet when the server has it off (404 calendar_unavailable)", async () => {
    state.answer = async () => {
      throw new ApiError(404, "calendar_unavailable", "Google Calendar is not available.");
    };
    const html = await render();
    expect(html).toContain("Google Calendar is not available yet.");
    expect(html).not.toContain("Connect Google Calendar");
    expect(html).toContain("Preferences for your notes and transcripts.");
  });

  it("sends a visitor whose session ended meanwhile to /login", async () => {
    state.answer = async () => {
      throw new ApiError(401, "not_authenticated", "Please log in.");
    };
    await expect(render()).rejects.toThrow("redirect:/login");
    state.authenticated = false;
    await expect(render()).rejects.toThrow("redirect:/login");
    state.authenticated = true;
  });

  it("lets any other failure reach the error page", async () => {
    state.answer = async () => {
      throw new ApiError(500, "internal_error", "Something went wrong.");
    };
    await expect(render()).rejects.toThrow("Something went wrong.");
  });
});

describe("server and client code stay apart", () => {
  // Next refuses (at request time, not in vitest) a server page calling a function that lives in a
  // "use client" file; the first run of this page failed that way. Pages import values only from
  // plain modules, and a client component only as a component.
  it("reads the redirect's query in a plain module, not in the client component", () => {
    const page = read(join(WEB, "app/settings/page.tsx"));
    const component = read(join(WEB, "components/app/settings-calendar.tsx"));
    const plain = read(join(WEB, "lib/calendar-notice.ts"));
    expect(component.startsWith('"use client"')).toBe(true);
    expect(plain).not.toMatch(/use client/);
    expect(page).toMatch(/import \{ SettingsCalendar \} from "@\/components\/app\/settings-calendar"/);
    expect(page).toMatch(/calendarNotice\b[^;]*from "@\/lib\/calendar-notice"/);
  });
});
