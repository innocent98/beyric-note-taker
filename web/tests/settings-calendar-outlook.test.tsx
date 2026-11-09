// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsCalendar } from "@/components/app/settings-calendar";
import { SetupWorkspace } from "@/components/auth/setup-workspace";
import { calendarNotice } from "@/lib/calendar-notice";
import { ApiError } from "@/lib/api/client";
import type { CalendarState } from "@/lib/api/types";
import { calendarBody } from "./helpers";

// Outlook on the Settings Calendar card and the first-run rows. The state bodies are the captured
// Google ones with the new fields added by hand (`microsoft_enabled`, and `provider` /
// `account_email` for an Outlook connection): the live Outlook captures replace them when the M0
// run and the live connect exist (docs/fe-integration-guide-calendar-outlook.md says which is which).

const mocks = vi.hoisted(() => ({
  connect: vi.fn(),
  disconnect: vi.fn(),
  settings: vi.fn(),
  update: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: mocks.replace, refresh: mocks.refresh }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    team: { update: mocks.update },
    calendar: { connect: mocks.connect, disconnect: mocks.disconnect, settings: mocks.settings },
  },
}));

const base = calendarBody<CalendarState>("05-state-not-connected.json");
const googleConnected = calendarBody<CalendarState>("09-state-connected-with-upcoming.json");
const off: CalendarState = { ...base, microsoft_enabled: false };
const on: CalendarState = { ...base, microsoft_enabled: true };
const outlook: CalendarState = {
  ...googleConnected,
  microsoft_enabled: true,
  provider: "microsoft",
  account_email: "ada@outlook.example.test",
  upcoming: [],
};
const outlookDead: CalendarState = {
  ...outlook,
  connected: false,
  state: "needs_reconnect",
  state_text: "Microsoft access was removed. Connect again to carry on.",
  last_error: "Microsoft no longer lets Beyric read your calendar. Connect again to fix this.",
};

beforeEach(() => Object.values(mocks).forEach((fn) => fn.mockReset()));
afterEach(cleanup);

const view = (initial: CalendarState | null, extra: Partial<Parameters<typeof SettingsCalendar>[0]> = {}) =>
  render(<SettingsCalendar initial={initial} notice={null} navigate={vi.fn()} {...extra} />);

describe("while the server has Outlook off", () => {
  it.each([off, base])("looks exactly as before: one live button and Outlook switched off", (state) => {
    view(state);
    expect(screen.getByRole("button", { name: "Connect Google Calendar" }).hasAttribute("disabled")).toBe(false);
    const outlookButton = screen.getByRole("button", { name: "Connect Microsoft Outlook" });
    expect(outlookButton.hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("Not available yet.")).toBeTruthy();
    expect(screen.getByText(/asks Google for read-only access to the events on your calendars/)).toBeTruthy();
  });
});

describe("with Outlook on, not connected", () => {
  it("draws the designer's two buttons side by side, both live, and no 'not available' line", () => {
    view(on);
    const google = screen.getByRole("button", { name: "Connect Google Calendar" });
    const microsoft = screen.getByRole("button", { name: "Connect Microsoft Outlook" });
    expect(google.hasAttribute("disabled")).toBe(false);
    expect(microsoft.hasAttribute("disabled")).toBe(false);
    expect(google.parentElement).toBe(microsoft.parentElement); // one row of buttons
    expect(screen.queryByText("Not available yet.")).toBeNull();
    expect(screen.getByText(/asks Google or Microsoft for read-only access to the events on your main calendar/)).toBeTruthy();
  });

  it("connects Outlook: only its own button says Connecting..., both are disabled, and the browser goes to Microsoft", async () => {
    let finish: (value: unknown) => void = () => {};
    mocks.connect.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const navigate = vi.fn();
    view(on, { navigate });
    await userEvent.click(screen.getByRole("button", { name: "Connect Microsoft Outlook" }));
    expect(mocks.connect).toHaveBeenCalledWith("microsoft");
    const working = await screen.findByRole("button", { name: "Connecting..." });
    expect(working.hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Connect Google Calendar" }).hasAttribute("disabled")).toBe(true);
    finish({ authorize_url: "https://login.example/consent", show_unverified_notice: false });
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("https://login.example/consent"));
  });

  it("connects Google with the Google provider", async () => {
    mocks.connect.mockResolvedValue({ authorize_url: "https://accounts.example/consent", show_unverified_notice: false });
    const navigate = vi.fn();
    view(on, { navigate });
    await userEvent.click(screen.getByRole("button", { name: "Connect Google Calendar" }));
    expect(mocks.connect).toHaveBeenCalledWith("google");
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("https://accounts.example/consent"));
  });

  it("explains Microsoft's unverified-publisher screen first when the server asks, then continues", async () => {
    mocks.connect.mockResolvedValue({ authorize_url: "https://login.example/consent", show_unverified_notice: true });
    const navigate = vi.fn();
    view(on, { navigate });
    await userEvent.click(screen.getByRole("button", { name: "Connect Microsoft Outlook" }));
    expect(await screen.findByText(/Microsoft may warn that Beyric comes from a publisher it has not verified yet/)).toBeTruthy();
    expect(navigate).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Continue to Microsoft" }));
    expect(navigate).toHaveBeenCalledWith("https://login.example/consent");
  });

  it("shows the server's refusal and lets the person try again", async () => {
    mocks.connect.mockRejectedValue(
      new ApiError(409, "calendar_already_connected", "Another calendar is already connected. Disconnect it first."),
    );
    view(on);
    await userEvent.click(screen.getByRole("button", { name: "Connect Microsoft Outlook" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Another calendar is already connected");
    expect(screen.getByRole("button", { name: "Connect Microsoft Outlook" }).hasAttribute("disabled")).toBe(false);
  });
});

describe("a connected Outlook calendar", () => {
  it("is the designer's card: the provider name and 'address · Connected'", () => {
    view(outlook);
    expect(screen.getByText("Microsoft Outlook")).toBeTruthy();
    expect(screen.getByText("ada@outlook.example.test · Connected")).toBeTruthy();
    expect(screen.queryByText("Google Calendar")).toBeNull();
    expect(screen.queryByRole("button", { name: /Connect/ })).toBeNull();
    expect(screen.getByText("Join automatically")).toBeTruthy();
  });

  it("disconnects with the Microsoft provider and says where to remove the app at Microsoft", async () => {
    mocks.disconnect.mockResolvedValue({ status: "disconnected", revoked: false, reason: "provider_has_no_revoke" });
    view(outlook);
    await userEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    const group = screen.getByRole("group", { name: "Disconnect Microsoft Outlook?" });
    expect(within(group).getByText(/Beyric forgets your Microsoft connection/)).toBeTruthy();
    await userEvent.click(within(group).getByRole("button", { name: "Disconnect" }));
    expect(mocks.disconnect).toHaveBeenCalledWith("microsoft");
    expect(await screen.findByText("Microsoft Outlook disconnected.")).toBeTruthy();
    expect(screen.getByText(/Microsoft does not let Beyric remove its own access/)).toBeTruthy();
    expect(screen.queryByText(/myaccount\.google\.com/)).toBeNull();
    // the card is the not-connected one again, with both buttons
    expect(screen.getByRole("button", { name: "Connect Microsoft Outlook" })).toBeTruthy();
  });

  it("does not show the remove-it-yourself note when Microsoft somehow confirmed", async () => {
    mocks.disconnect.mockResolvedValue({ status: "disconnected", revoked: true });
    view(outlook);
    await userEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    await userEvent.click(within(screen.getByRole("group")).getByRole("button", { name: "Disconnect" }));
    expect(await screen.findByText("Microsoft Outlook disconnected.")).toBeTruthy();
    expect(screen.queryByText(/Microsoft does not let Beyric/)).toBeNull();
  });

  it("when it needs connecting again, offers the Microsoft reconnect with the server's words", async () => {
    mocks.connect.mockResolvedValue({ authorize_url: "https://login.example/consent", show_unverified_notice: false });
    const navigate = vi.fn();
    view(outlookDead, { navigate });
    expect(screen.getAllByText("Microsoft access was removed. Connect again to carry on.").length).toBeGreaterThan(0);
    expect(screen.getByText("ada@outlook.example.test · Needs to connect again")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Connect Microsoft Outlook again" }));
    expect(mocks.connect).toHaveBeenCalledWith("microsoft");
    await waitFor(() => expect(navigate).toHaveBeenCalled());
  });
});

describe("a connected Google calendar is unchanged", () => {
  it("keeps its name and disconnects with the Google provider", async () => {
    mocks.disconnect.mockResolvedValue({ status: "disconnected", revoked: false });
    view({ ...googleConnected, microsoft_enabled: true });
    expect(screen.getByText("Google Calendar")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(screen.getByRole("group", { name: "Disconnect Google Calendar?" })).toBeTruthy();
    await userEvent.click(within(screen.getByRole("group")).getByRole("button", { name: "Disconnect" }));
    expect(mocks.disconnect).toHaveBeenCalledWith("google");
    expect(await screen.findByText(/myaccount\.google\.com\/permissions/)).toBeTruthy();
  });
});

describe("what the callback's redirect says", () => {
  const words = (code: string, state: CalendarState = on) => {
    const { container } = view(state, { notice: { kind: "error", code } });
    return container.querySelector(".alert-text")?.textContent ?? "";
  };

  it("names the connected provider", () => {
    view(outlook, { notice: { kind: "connected" } });
    expect(screen.getByText("Microsoft Outlook connected.")).toBeTruthy();
  });

  it("has words for each Outlook refusal and never prints a code", () => {
    expect(words("microsoft_denied")).toBe("You did not connect your calendar.");
    cleanup();
    expect(words("microsoft_failed")).toBe("Something went wrong connecting Microsoft Outlook. Try again.");
    cleanup();
    expect(words("no_mailbox")).toContain("has no calendar Beyric can read");
    cleanup();
    expect(words("tenant_not_allowed")).toContain("organisation Beyric cannot connect yet");
    cleanup();
    expect(words("already_connected")).toContain("Another calendar is already connected");
    for (const code of ["microsoft_denied", "microsoft_failed", "no_mailbox", "tenant_not_allowed", "already_connected"]) {
      cleanup();
      expect(words(code)).not.toContain(code);
    }
  });

  it("names Microsoft when a different Microsoft account is already connected", () => {
    const { container } = view(outlook, { notice: { kind: "error", code: "different_account" } });
    expect(container.querySelector(".alert-text")?.textContent).toBe(
      "A different Microsoft account is already connected (ada@outlook.example.test). To use another one, disconnect first.",
    );
  });

  it("reads the new codes from the query and still turns an unknown one into google_failed", () => {
    for (const code of ["microsoft_denied", "microsoft_failed", "no_mailbox", "tenant_not_allowed", "already_connected"]) {
      expect(calendarNotice({ calendar_error: code })).toEqual({ kind: "error", code });
    }
    expect(calendarNotice({ calendar_error: "<script>alert(1)</script>" })).toEqual({ kind: "error", code: "google_failed" });
    expect(calendarNotice({ calendar: "connected" })).toEqual({ kind: "connected" });
  });
});

describe("the first-run setup rows", () => {
  it("keep Outlook switched off while the server has it off", () => {
    render(<SetupWorkspace calendarConnected={false} />);
    expect(screen.getByText("Not available yet.")).toBeTruthy();
    expect(screen.getByText("Microsoft Outlook").closest("button")?.hasAttribute("disabled")).toBe(true);
  });

  it("make Outlook a live row when the server has it on, and connect it", async () => {
    mocks.update.mockResolvedValue({});
    mocks.connect.mockResolvedValue({ authorize_url: "https://login.example/consent", show_unverified_notice: false });
    const navigate = vi.fn();
    render(<SetupWorkspace calendarConnected={false} microsoftEnabled navigate={navigate} />);
    expect(screen.queryByText("Not available yet.")).toBeNull();
    const row = screen.getByText("Microsoft Outlook").closest("button")!;
    expect(row.hasAttribute("disabled")).toBe(false);
    expect(within(row).getByText("Connect")).toBeTruthy();
    await userEvent.type(screen.getByLabelText("Workspace name"), "Beyric");
    await userEvent.click(row);
    expect(mocks.update).toHaveBeenCalledWith({ name: "Beyric" });
    expect(mocks.connect).toHaveBeenCalledWith("microsoft");
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("https://login.example/consent"));
  });

  it("show Connected on the connected provider's row and keep the other row disabled", () => {
    render(<SetupWorkspace calendarConnected microsoftEnabled connectedProvider="microsoft" />);
    const outlookRow = screen.getByText("Microsoft Outlook").closest("button")!;
    const googleRow = screen.getByText("Google Calendar").closest("button")!;
    expect(within(outlookRow).getByText("Connected")).toBeTruthy();
    expect(within(googleRow).getByText("Connect")).toBeTruthy();
    expect(googleRow.hasAttribute("disabled")).toBe(true);
    expect(outlookRow.hasAttribute("disabled")).toBe(true);
  });
});
