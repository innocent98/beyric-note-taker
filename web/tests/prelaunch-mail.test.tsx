// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "@/components/app/shell";
import { NotificationsView, noticeLink } from "@/components/app/notifications-view";
import { EmailSettingsSection } from "@/components/app/email-settings-section";
import { ApiError, createApi } from "@/lib/api/client";
import type { EmailPreferences, NoticeList } from "@/lib/api/types";
import { prelaunchCapture } from "./helpers";

// Every body below is a response captured from the running API
// (docs/fe-captures/prelaunch-mail-prefs/, made by scripts/capture_prelaunch_mail_api.py).

const mocks = vi.hoisted(() => ({ setEmail: vi.fn(), readAll: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({
  api: { settings: { email: { set: mocks.setEmail } }, notifications: { readAll: mocks.readAll } },
}));

const body = <T,>(file: string) => prelaunchCapture(file).response.body as T;
const on = body<EmailPreferences>("06-settings-read-default.json");
const off = body<EmailPreferences>("07-settings-turn-off.json");
const notices = body<NoticeList>("22-notifications-cap-notice.json").notifications;

beforeEach(() => Object.values(mocks).forEach((fn) => fn.mockReset()));

describe("settings: email section", () => {
  const SWITCH = { name: "Email me when my notes are ready" };

  it("shows the switch as the server has it, in a section the mail's link points at", () => {
    const { container } = render(<EmailSettingsSection email={on} />);
    expect(container.querySelector("section#email-preferences")).not.toBeNull();
    expect(screen.getByText("Email")).toBeTruthy();
    expect(screen.getByRole("switch", SWITCH).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText(/Emails about signing in and your password are always sent/)).toBeTruthy();
  });

  it("turns it off and says it was saved, using the value the server answered", async () => {
    mocks.setEmail.mockResolvedValue(off);
    render(<EmailSettingsSection email={on} />);
    await userEvent.click(screen.getByRole("switch", SWITCH));
    expect(mocks.setEmail).toHaveBeenCalledWith(false);
    expect(await screen.findByText("Saved.")).toBeTruthy();
    expect(screen.getByRole("switch", SWITCH).getAttribute("aria-checked")).toBe("false");
  });

  it("turns it back on with the opposite value", async () => {
    mocks.setEmail.mockResolvedValue(on);
    render(<EmailSettingsSection email={off} />);
    await userEvent.click(screen.getByRole("switch", SWITCH));
    expect(mocks.setEmail).toHaveBeenCalledWith(true);
    await vi.waitFor(() => expect(screen.getByRole("switch", SWITCH).getAttribute("aria-checked")).toBe("true"));
  });

  it("leaves the switch where it was and shows the error when the save fails", async () => {
    mocks.setEmail.mockRejectedValue(new ApiError(0, "network_error", "Could not reach the server. Check your connection and try again."));
    render(<EmailSettingsSection email={on} />);
    await userEvent.click(screen.getByRole("switch", SWITCH));
    expect((await screen.findByRole("alert")).textContent).toContain("Could not reach the server");
    expect(screen.getByRole("switch", SWITCH).getAttribute("aria-checked")).toBe("true");
    expect(screen.queryByText("Saved.")).toBeNull();
  });

  it("does not show a stale CSRF failure as a raw code", async () => {
    mocks.setEmail.mockRejectedValue(new ApiError(403, "csrf_rejected", "Missing or wrong X-CSRF-Token header."));
    render(<EmailSettingsSection email={on} />);
    await userEvent.click(screen.getByRole("switch", SWITCH));
    expect((await screen.findByRole("alert")).textContent).toBe("Your session changed. Reload the page and try again.");
  });
});

describe("notifications", () => {
  it("lists the captured notices, newest first, with a link where there is somewhere to go", () => {
    render(<NotificationsView initial={notices} />);
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("A notes email was not sent")).toBeTruthy();
    expect(within(rows[0]).getByRole("link", { name: "Open the meeting" }).getAttribute("href")).toBe("/meetings/2");
    expect(within(rows[1]).getByText("We updated our Privacy Policy")).toBeTruthy();
    expect(within(rows[1]).getByRole("link", { name: "Read the Privacy Policy" }).getAttribute("href")).toBe("/privacy");
    expect(within(rows[1]).getAllByText("New")).toHaveLength(1);
  });

  it("marks everything read once the server says so", async () => {
    mocks.readAll.mockResolvedValue(body("24-notifications-read-all.json"));
    render(<NotificationsView initial={notices} />);
    await userEvent.click(screen.getByRole("button", { name: "Mark all as read" }));
    expect(mocks.readAll).toHaveBeenCalledTimes(1);
    await vi.waitFor(() => expect(screen.queryAllByText("New")).toHaveLength(0));
    expect(screen.queryAllByText("New")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Mark all as read" })).toBeNull();
  });

  it("keeps the notices unread and says so when marking fails", async () => {
    mocks.readAll.mockRejectedValue(new ApiError(500, "server_error", "Something went wrong."));
    render(<NotificationsView initial={notices} />);
    await userEvent.click(screen.getByRole("button", { name: "Mark all as read" }));
    expect((await screen.findByRole("alert")).textContent).toBeTruthy();
    expect(screen.getAllByText("New")).toHaveLength(2);
  });

  it("says so when there are none", () => {
    render(<NotificationsView initial={[]} />);
    expect(screen.getByText("Nothing yet.")).toBeTruthy();
  });

  it("links a notice to nothing the API did not give it", () => {
    expect(noticeLink({ ...notices[0], kind: "usage.hours_low", meeting_id: null })).toBeNull();
    expect(noticeLink({ ...notices[0], meeting_id: 7 })?.href).toBe("/meetings/7");
  });

  it("is in the sidebar with the unread count, which a screen reader hears as unread", () => {
    const html = renderToStaticMarkup(<AppShell current="notifications" unread={3}>x</AppShell>);
    expect(html).toContain('<span class="nav-badge" aria-hidden="true">3</span>');
    expect(html).toContain(", 3 unread");
    expect(html).toMatch(/href="\/settings"/);
    expect(renderToStaticMarkup(<AppShell current="meetings">x</AppShell>)).not.toContain("nav-badge");
  });
});

describe("api client", () => {
  it("sends the preference as a PUT with the CSRF token and a boolean body", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      calls.push({ url: String(input), init });
      const json = String(input) === "/api/session" ? { authenticated: true, csrf_token: "tok", user: null, usage: null } : off;
      return new Response(JSON.stringify(json), { status: 200, headers: { "content-type": "application/json" } });
    });
    const api = createApi({ fetch: fetchMock as unknown as typeof fetch });
    expect(await api.settings.email.set(false)).toEqual(off);
    const put = calls.find((c) => c.init.method === "PUT")!;
    expect(put.url).toBe("/api/settings/email");
    expect(JSON.parse(String(put.init.body))).toEqual({ notify_email: false });
    expect((put.init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok");
  });
});
