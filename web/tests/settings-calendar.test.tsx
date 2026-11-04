// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsCalendar } from "@/components/app/settings-calendar";
import { calendarNotice } from "@/lib/calendar-notice";
import { ApiError } from "@/lib/api/client";
import type { CalendarState, ConnectStart, DisconnectResult } from "@/lib/api/types";
import { calendarBody, connectBody } from "./helpers";

// The settings screen is fed the bodies captured from the running app
// (docs/fe-captures/calendar-state/ and calendar-connect/, cited in
// docs/fe-integration-guide-calendar-settings.md).

const mocks = vi.hoisted(() => ({ get: vi.fn(), connect: vi.fn(), disconnect: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({
  api: { calendar: { get: mocks.get, connect: mocks.connect, disconnect: mocks.disconnect } },
}));

const notConnected = calendarBody<CalendarState>("05-state-not-connected.json");
const justConnected = calendarBody<CalendarState>("08-state-connected-before-first-sync.json");
const withEvents = calendarBody<CalendarState>("09-state-connected-with-upcoming.json");
const organizerRule = calendarBody<CalendarState>("10-state-rule-organizer.json");
const noRule = calendarBody<CalendarState>("11-state-rule-none.json");
const revokedError = calendarBody<CalendarState>("12-state-last-sync-failed-revoked.json");
const degraded = calendarBody<CalendarState>("14-state-degraded.json");
const needsReconnect = calendarBody<CalendarState>("15-state-needs-reconnect-revoked.json");
const disconnected = calendarBody<DisconnectResult>("20-disconnect.json");
const unverified = connectBody<ConnectStart>("08-first-connect-connect.json");
const verified = connectBody<ConnectStart>("32-connect-verified-app.json");

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
});
afterEach(cleanup);

const view = (initial: CalendarState | null, extra: Partial<Parameters<typeof SettingsCalendar>[0]> = {}) =>
  render(<SettingsCalendar initial={initial} notice={null} navigate={vi.fn()} {...extra} />);

describe("not connected", () => {
  it("shows the designer's Calendar section with the Connect Google Calendar button", () => {
    view(notConnected);
    expect(screen.getByRole("heading", { level: 2, name: "Calendar" })).toBeTruthy();
    expect(
      screen.getByText(
        "The note taker reads your calendar and joins meetings that have a Google Meet, Microsoft Teams or Zoom link.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Connect Google Calendar" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Disconnect/ })).toBeNull();
    expect(screen.queryByText("Join automatically")).toBeNull();
  });

  it("says in plain words what Google is asked for and what is kept", () => {
    view(notConnected);
    expect(screen.getByText(/read-only access to the events on your calendars/)).toBeTruthy();
    expect(screen.getByText(/never adds, changes or deletes anything on your calendar/)).toBeTruthy();
    expect(screen.getByText(/does not keep guest email addresses or event descriptions/)).toBeTruthy();
  });

  it("does not draw an Outlook button (it is not built)", () => {
    view(notConnected);
    expect(document.body.textContent).not.toMatch(/Outlook/);
  });

  it("goes straight to Google when the app is verified", async () => {
    mocks.connect.mockResolvedValue(verified);
    const navigate = vi.fn();
    view(notConnected, { navigate });
    await userEvent.click(screen.getByRole("button", { name: "Connect Google Calendar" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(verified.authorize_url));
    expect(mocks.connect).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/hasn.t verified/)).toBeNull();
  });

  it("explains Google's unverified-app warning first, and only goes on when asked", async () => {
    mocks.connect.mockResolvedValue(unverified);
    const navigate = vi.fn();
    view(notConnected, { navigate });
    await userEvent.click(screen.getByRole("button", { name: "Connect Google Calendar" }));
    expect(await screen.findByText(/Google will warn that it has not verified Beyric/)).toBeTruthy();
    expect(navigate).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Continue to Google" }));
    expect(navigate).toHaveBeenCalledWith(unverified.authorize_url);
  });

  it("shows the server's words when connecting fails, and the button works again", async () => {
    mocks.connect.mockRejectedValue(
      new ApiError(503, "calendar_unavailable", "Google Calendar cannot be connected right now."),
    );
    const navigate = vi.fn();
    view(notConnected, { navigate });
    await userEvent.click(screen.getByRole("button", { name: "Connect Google Calendar" }));
    expect(await screen.findByText("Google Calendar cannot be connected right now.")).toBeTruthy();
    expect(navigate).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Connect Google Calendar" }).hasAttribute("disabled")).toBe(false);
  });

  it("does not start a second connect while one is in flight", async () => {
    mocks.connect.mockReturnValue(new Promise(() => {}));
    view(notConnected);
    await userEvent.click(screen.getByRole("button", { name: "Connect Google Calendar" }));
    expect(screen.getByRole("button", { name: "Connecting..." }).hasAttribute("disabled")).toBe(true);
    expect(mocks.connect).toHaveBeenCalledTimes(1);
  });
});

describe("not available", () => {
  it("says so plainly while the feature is off, with no button to press", () => {
    view(null);
    expect(screen.getByText("Google Calendar is not available yet.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Connect/ })).toBeNull();
  });
});

describe("connected", () => {
  it("shows the provider row with the Google address and Connected, and Disconnect", () => {
    view(justConnected);
    expect(screen.getByText("Google Calendar")).toBeTruthy();
    expect(screen.getByText("grace@example.test · Connected")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Connect Google Calendar" })).toBeNull();
  });

  it("says nothing has synced yet, or when the last update was", () => {
    view(justConnected);
    expect(screen.getByText(/Waiting for the first update/)).toBeTruthy();
    cleanup();
    view(withEvents);
    expect(screen.getByText(/Last updated 04 Nov 2026, 13:08 UTC/)).toBeTruthy();
  });

  it("shows the three auto-join rules read only, with the person's current one chosen", () => {
    view(withEvents);
    const group = screen.getByRole("radiogroup", { name: "Join automatically" });
    const radios = within(group).getAllByRole("radio");
    expect(radios.map((r) => r.textContent)).toEqual([
      "All meetings with a video linkEvery event on your calendar with a meeting link.",
      "Only meetings I organiseSkip meetings other people invited you to.",
      "Only when I turn it onPick meetings one by one from the Upcoming list.",
    ]);
    expect(radios.map((r) => r.getAttribute("aria-checked"))).toEqual(["true", "false", "false"]);
    expect(radios.every((r) => r.getAttribute("aria-disabled") === "true")).toBe(true);
  });

  it("follows the stored rule", () => {
    view(organizerRule);
    expect(within(screen.getByRole("radiogroup")).getAllByRole("radio").map((r) => r.getAttribute("aria-checked"))).toEqual(["false", "true", "false"]);
    cleanup();
    view(noRule);
    expect(within(screen.getByRole("radiogroup")).getAllByRole("radio").map((r) => r.getAttribute("aria-checked"))).toEqual(["false", "false", "true"]);
  });

  it("does not draw the template select or the upcoming list here (they are not part of this section)", () => {
    view(withEvents);
    expect(screen.queryByText("Template for calendar meetings")).toBeNull();
    expect(screen.queryByText("Weekly planning")).toBeNull();
    expect(screen.queryByText("Upcoming")).toBeNull();
  });

  it("shows the last failure in the server's plain words", () => {
    view(revokedError);
    expect(
      screen.getByText("Google no longer lets Beyric read your calendar. Connect again to fix this."),
    ).toBeTruthy();
  });

  it("shows the server's sentence for a calendar that has not been read lately", () => {
    view(degraded);
    expect(screen.getByText("Connected, but your calendar has not been read lately.")).toBeTruthy();
  });

  it("never shows an attendee address, a link or a passcode (the answer has none)", () => {
    view(withEvents);
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/PLACEHOLDERPASSCODE|zoom\.us|pwd=|stranger@|colleague@/);
  });
});

describe("needs a reconnect", () => {
  it("says why and offers Connect again, without also showing the old error", () => {
    view(needsReconnect);
    expect(screen.getByText("Google access was removed. Connect again to carry on.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Connect Google Calendar again" })).toBeTruthy();
    expect(screen.queryByText(/The last check of your calendar did not work/)).toBeNull();
    expect(screen.getAllByText(/grace@example\.test/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeTruthy();
  });

  it("starts the connect flow again from that button", async () => {
    mocks.connect.mockResolvedValue(verified);
    const navigate = vi.fn();
    view(needsReconnect, { navigate });
    await userEvent.click(screen.getByRole("button", { name: "Connect Google Calendar again" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(verified.authorize_url));
  });
});

describe("disconnecting", () => {
  it("asks first and says what is deleted and what stays", async () => {
    view(withEvents);
    await userEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    const sure = screen.getByRole("group", { name: "Disconnect Google Calendar?" });
    expect(within(sure).getByText(/forgets your Google connection/)).toBeTruthy();
    expect(within(sure).getByText(/deletes the calendar events it copied/)).toBeTruthy();
    expect(within(sure).getByText(/cancels the meetings it planned to join/)).toBeTruthy();
    expect(within(sure).getByText(/Meetings already recorded and your notes stay/)).toBeTruthy();
    expect(mocks.disconnect).not.toHaveBeenCalled();
  });

  it("changes nothing when the person keeps the connection", async () => {
    view(withEvents);
    await userEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    await userEvent.click(screen.getByRole("button", { name: "Keep connected" }));
    expect(screen.queryByRole("group", { name: "Disconnect Google Calendar?" })).toBeNull();
    expect(mocks.disconnect).not.toHaveBeenCalled();
    expect(screen.getAllByText(/grace@example\.test/).length).toBeGreaterThan(0);
  });

  it("disconnects, then shows the not-connected screen with the Connect button", async () => {
    mocks.disconnect.mockResolvedValue(disconnected);
    view(withEvents);
    await userEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    await userEvent.click(within(screen.getByRole("group", { name: "Disconnect Google Calendar?" })).getByRole("button", { name: "Disconnect" }));
    expect(await screen.findByRole("button", { name: "Connect Google Calendar" })).toBeTruthy();
    expect(mocks.disconnect).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Weekly planning")).toBeNull();
    expect(screen.queryByText(/grace@example\.test/)).toBeNull();
    expect(screen.getByText("Google Calendar disconnected.")).toBeTruthy();
    expect(screen.queryByText(/myaccount\.google\.com/)).toBeNull();
  });

  it("tells the person to remove Beyric at Google when Google could not be asked", async () => {
    mocks.disconnect.mockResolvedValue({ status: "disconnected", revoked: false });
    view(withEvents);
    await userEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    await userEvent.click(within(screen.getByRole("group", { name: "Disconnect Google Calendar?" })).getByRole("button", { name: "Disconnect" }));
    const note = await screen.findByText(/could not ask Google to remove its access/);
    expect(note.textContent).toMatch(/myaccount\.google\.com\/permissions/);
    expect(screen.getByRole("link", { name: "myaccount.google.com/permissions" }).getAttribute("href")).toBe(
      "https://myaccount.google.com/permissions",
    );
  });

  it("keeps the connected screen and shows the error when the call fails", async () => {
    mocks.disconnect.mockRejectedValue(new ApiError(0, "network_error", "Could not reach the server. Check your connection and try again."));
    view(withEvents);
    await userEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    await userEvent.click(within(screen.getByRole("group", { name: "Disconnect Google Calendar?" })).getByRole("button", { name: "Disconnect" }));
    expect(await screen.findByText("Could not reach the server. Check your connection and try again.")).toBeTruthy();
    expect(screen.getAllByText(/grace@example\.test/).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Connect Google Calendar" })).toBeNull();
  });

  it("sends one request however often the button is pressed", async () => {
    mocks.disconnect.mockReturnValue(new Promise(() => {}));
    view(withEvents);
    await userEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    const sure = screen.getByRole("group", { name: "Disconnect Google Calendar?" });
    await userEvent.click(within(sure).getByRole("button", { name: "Disconnect" }));
    expect(within(sure).getByRole("button", { name: "Disconnecting..." }).hasAttribute("disabled")).toBe(true);
    expect(mocks.disconnect).toHaveBeenCalledTimes(1);
  });
});

describe("what the connect page's redirect says", () => {
  it("reads calendar=connected", () => {
    expect(calendarNotice({ calendar: "connected" })).toEqual({ kind: "connected" });
  });

  it("reads every calendar_error the callback can send", () => {
    for (const code of [
      "google_denied",
      "scope_missing",
      "no_refresh_token",
      "google_failed",
      "different_account",
      "wrong_account",
    ]) {
      expect(calendarNotice({ calendar_error: code })).toEqual({ kind: "error", code });
    }
  });

  it("treats a code it does not know as a failed connect, never printing it", () => {
    expect(calendarNotice({ calendar_error: "<script>x</script>" })).toEqual({ kind: "error", code: "google_failed" });
  });

  it("takes the first value when a name is repeated, and nothing when neither is present", () => {
    expect(calendarNotice({ calendar_error: ["scope_missing", "google_failed"] })).toEqual({
      kind: "error",
      code: "scope_missing",
    });
    expect(calendarNotice({})).toBeNull();
    expect(calendarNotice({ calendar: "nonsense" })).toBeNull();
  });
});

describe("the message after coming back from Google", () => {
  it("welcomes a connected calendar", () => {
    view(justConnected, { notice: { kind: "connected" } });
    expect(screen.getByText("Google Calendar connected.")).toBeTruthy();
  });

  it("says plainly that cancelling is fine, without error styling", () => {
    view(notConnected, { notice: { kind: "error", code: "google_denied" } });
    const note = screen.getByText("You did not connect your calendar.");
    expect(note.closest(".alert-error")).toBeNull();
  });

  it.each([
    ["scope_missing", /To join your meetings automatically we need permission to read your calendar events\. Try again and leave the calendar box ticked\./],
    ["no_refresh_token", /Google did not finish connecting\. Remove Beyric at myaccount\.google\.com\/permissions and try again\./],
    ["google_failed", /Something went wrong connecting Google\. Try again\./],
    ["wrong_account", /This connection was started from a different account\. Sign in with the right account and start again\./],
  ])("explains %s", (code, words) => {
    view(notConnected, { notice: { kind: "error", code } });
    expect(screen.getByText(words)).toBeTruthy();
  });

  it("names the account that is already connected for different_account", () => {
    view(justConnected, { notice: { kind: "error", code: "different_account" } });
    expect(
      screen.getByText(
        "A different Google account is already connected (grace@example.test). To use another one, disconnect first.",
      ),
    ).toBeTruthy();
  });

  it("still makes sense for different_account when no address is known", () => {
    view(notConnected, { notice: { kind: "error", code: "different_account" } });
    expect(screen.getByText(/A different Google account is already connected\. To use another one, disconnect first\./)).toBeTruthy();
  });

  it("never prints an unknown code", () => {
    view(notConnected, { notice: { kind: "error", code: "<b>boom</b>" } });
    expect(document.body.textContent).not.toContain("boom");
  });
});
