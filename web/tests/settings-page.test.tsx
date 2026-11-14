import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import SettingsPage, { metadata } from "@/app/settings/page";
import { AppShell } from "@/components/app/shell";
import { ApiError } from "@/lib/api/client";
import { APP_PATHS, NEXT_PAGES, isNextPage } from "@/lib/routes";
import type { CalendarState } from "@/lib/api/types";
import { calendarBody, read, sprintsBody, WEB } from "./helpers";
import { join } from "node:path";

// The server half of /settings/calendar, and where it sits in the app's routes and sidebar.

const state = vi.hoisted(() => ({
  authenticated: true,
  answer: null as null | (() => Promise<unknown>),
  calls: 0,
  templates: null as null | (() => Promise<unknown>),
  teams: null as null | { teams: unknown[] },
  teamOverride: {} as Record<string, unknown>,
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  redirect: (to: string) => {
    throw new Error(`redirect:${to}`);
  },
  notFound: () => {
    throw new Error("not-found");
  },
  useRouter: () => ({ refresh: vi.fn(), push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/api/server", () => {
  const owner = async () => {
    if (!state.authenticated) throw new Error("redirect:/login");
    return {
      calendar: {
        get: async () => {
          state.calls += 1;
          return state.answer!();
        },
      },
      templates: { list: async () => (state.templates ? state.templates() : []) },
      sprints: { list: async () => sprintsBody("09-sprints-first.json") },
      settings: {
        email: { get: async () => ({ notify_email: true }) },
        language: { get: async () => ({ default_language: "en" }) },
        timeZone: { get: async () => ({ time_zone: "UTC", is_set: false }) },
        tasks: { get: async () => ({ auto_create_tasks: true, email_assignees: true }) },
        reminders: { get: async () => ({ notify_reminders: true }) },
      },
      integrations: {
        linear: {
          get: async () => ({ connected: false, status: null, last_error: null, last_synced_at: null, team_name: null, can_manage: true }),
        },
        slack: {
          get: async () => ({ connected: false, status: null, last_error: null, last_posted_at: null, can_manage: true }),
        },
      },
      team: { get: async () => ({ id: 2, name: "My team", share_new_meetings: false, reminders_on: false, reminder_timing: "both", sprint_length: "week5", sprint_custom_days: 10, sprint_align_weekdays: true, my_role: "owner", member_count: 2, pending_invites: 0, can_manage: true, can_change_roles: true, ...state.teamOverride }) },
    };
  };
  return {
  requireOwner: owner,
  requireTeamOwner: async () => ({ api: await owner(), teams: state.teams, teamId: null }),
  getReviewOpen: async () => 2,
  getTasksNew: async () => 0,
  getUnreadNotifications: async () => null,
  getShellUser: async () => null,
  getShellTeams: async () => state.teams?.teams ?? null,
  getTimeZone: async () => null,
  load: <T,>(call: Promise<T>) => call,
  };
});

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
  it("draws the Email section below the Calendar section, with the notes-ready switch on", async () => {
    state.answer = async () => connected;
    const html = await render();
    expect(html).toContain('id="email-preferences"');
    expect(html.indexOf("Join automatically")).toBeLessThan(html.indexOf('id="email-preferences"'));
    expect(html).toMatch(/role="switch"[^>]*aria-checked="true"/);
  });

  it("draws the connected state from GET /api/calendar inside the app shell", async () => {
    state.answer = async () => connected;
    const html = await render();
    expect(html).toContain("Preferences for your notes and transcripts.");
    expect(html).toContain("grace@example.test");
    expect(html).toContain("Join automatically");
    expect(html).not.toContain("Weekly planning"); // the list is on the Meetings page
    expect(html).toContain(", 2 open");
  });

  it("draws the designer's other sections below Calendar, then Email (not in the design), whatever the calendar's state", async () => {
    const off = async () => {
      throw new ApiError(404, "calendar_unavailable", "Google Calendar is not available.");
    };
    for (const answer of [async () => connected, off]) {
      state.answer = answer;
      const html = await render();
      const order = [
        ...["Calendar", "Sprints and reminders", "Tasks", "Time zone", "Default notes language"].map((title) =>
          html.indexOf(`>${title}</h2>`),
        ),
        html.indexOf('id="email-preferences"'),
      ];
      expect(order.every((at) => at > -1)).toBe(true);
      expect([...order].sort((a, b) => a - b)).toEqual(order);
    }
  });

  it("gives the section the person's own templates for the select, sorted as the API gives them", async () => {
    state.answer = async () => connected;
    state.templates = async () => [
      { id: 2, name: "Client call", instructions: "x" },
      { id: 1, name: "General", instructions: "y" },
    ];
    const html = await render();
    expect(html).toContain("Template for calendar meetings");
    expect(html.indexOf("Client call")).toBeLessThan(html.indexOf("General"));
    state.templates = null;
  });

  it("is still drawn, without the select, when the templates cannot be read", async () => {
    state.answer = async () => connected;
    state.templates = async () => {
      throw new ApiError(500, "server_error", "Something went wrong on our side.");
    };
    const html = await render();
    expect(html).toContain("Join automatically");
    expect(html).not.toContain("Template for calendar meetings");
    state.templates = null;
  });

  it("draws the designer's other sections below Calendar, in order, whatever the calendar's state", async () => {
    const off = async () => {
      throw new ApiError(404, "calendar_unavailable", "Google Calendar is not available.");
    };
    for (const answer of [async () => connected, off]) {
      state.answer = answer;
      const html = await render();
      const order = ["Calendar", "Sprints and reminders", "Tasks", "Time zone", "Default notes language"].map((title) =>
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


describe("Slack and Linear belong to a team (multi-team)", () => {
  const withTeams = (team: Record<string, unknown>, on = true) => {
    state.answer = async () => connected;
    state.teams = on ? { teams: [{ id: 2, name: "My team", role: "owner", member_count: 1, is_personal: true, is_default: true, can_manage: true }] } : null;
    state.teamOverride = team;
  };
  afterEach(() => {
    state.teams = null;
    state.teamOverride = {};
  });

  it("a personal space gets neither the Slack nor the Linear row, and no 'not available yet' stand-ins", async () => {
    withTeams({ is_personal: true });
    const html = await render();
    expect(html).not.toContain("Post tasks to Slack");
    expect(html).not.toContain("Sync with your project tool");
    expect(html).not.toContain("Connect Slack");
  });

  it("an ordinary team keeps them", async () => {
    withTeams({ is_personal: false });
    const html = await render();
    expect(html).toContain("Post tasks to Slack");
    expect(html).toContain("Sync with your project tool");
  });

  it("with the switch off the team of one keeps them, personal or not (nothing changes)", async () => {
    withTeams({ is_personal: true }, false);
    const html = await render();
    expect(html).toContain("Post tasks to Slack");
    expect(html).toContain("Sync with your project tool");
  });
});
