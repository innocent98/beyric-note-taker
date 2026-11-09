// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BotInviteTip } from "@/components/app/bot-invite-tip";
import { MeetingView } from "@/components/app/meeting-view";
import { SettingsCalendar } from "@/components/app/settings-calendar";
import type { CalendarState, Meeting } from "@/lib/api/types";
import { inviteTipBody } from "./helpers";

// The tip is fed the bodies captured from the running app with the server set up for it and not
// (docs/fe-captures/bot-invite-tip/, scripts/capture_bot_invite_tip.py). The address in them is a
// placeholder; the web code has no address of its own.

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    milestones: { forMeeting: () => Promise.resolve({ milestones: [] }), confirm: vi.fn() },
    series: { list: vi.fn().mockResolvedValue({ timezone: "UTC", series: [] }), create: vi.fn() },
    meetings: { get: vi.fn() },
    calendar: { get: vi.fn(), connect: vi.fn(), disconnect: vi.fn(), settings: vi.fn() },
  },
}));

const calendarOn = inviteTipBody<CalendarState>("05-calendar-tip-on.json");
const calendarOff = inviteTipBody<CalendarState>("10-calendar-tip-off-address-unset.json");
const waiting = inviteTipBody<Meeting>("06-meeting-waiting-tip-on.json");
const notLetIn = inviteTipBody<Meeting>("07-meeting-refused-tip-on.json");
const zoom = inviteTipBody<Meeting>("08-meeting-zoom-tip-on.json");
const inside = inviteTipBody<Meeting>("09-meeting-inside-tip-on.json");
const waitingOff = inviteTipBody<Meeting>("11-meeting-waiting-tip-off-address-unset.json");
const ADDRESS = calendarOn.bot_invite_email as string;

const settings = (state: CalendarState) =>
  render(<SettingsCalendar initial={state} notice={null} navigate={vi.fn()} />);
const meeting = (m: Meeting) => render(<MeetingView initial={m} templates={[]} />);
const tip = () => screen.queryByRole("complementary", { name: /skip the wait/i });

function clipboard(writeText: (text: string) => Promise<void>) {
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
}

beforeEach(() => {
  Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
  Object.defineProperty(window, "isSecureContext", { value: false, configurable: true });
});
afterEach(() => {
  cleanup();
  window.getSelection()?.removeAllRanges();
});

describe("the tip itself", () => {
  it("shows the server's address in the words, the limit, and a Copy button", () => {
    render(<BotInviteTip email={ADDRESS} />);
    expect(ADDRESS).toBeTruthy();
    expect(screen.getByText(`to your event to skip the wait`, { exact: false })).toBeTruthy();
    expect(screen.getByText(ADDRESS).tagName).toBe("CODE");
    expect(screen.getByText(/the note taker gets in without waiting for you to admit it/)).toBeTruthy();
    expect(screen.getByText(/only skips the wait on Google Meet\. Zoom and Teams meetings still ask the host/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy address" })).toBeTruthy();
  });

  it.each([null, undefined, ""])("draws nothing without an address (%s)", (email) => {
    const { container } = render(<BotInviteTip email={email} />);
    expect(container.innerHTML).toBe("");
  });

  it("copies the address to the clipboard and says Copied", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    clipboard(writeText);
    render(<BotInviteTip email={ADDRESS} />);
    await userEvent.click(screen.getByRole("button", { name: "Copy address" }));
    expect(writeText).toHaveBeenCalledWith(ADDRESS);
    expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copied" }).getAttribute("aria-live")).toBe("polite");
  });

  it("when copying is refused it says so and selects the address so the person can copy it", async () => {
    clipboard(vi.fn().mockRejectedValue(new Error("denied"))); // and no execCommand fallback in jsdom
    render(<BotInviteTip email={ADDRESS} />);
    await userEvent.click(screen.getByRole("button", { name: "Copy address" }));
    expect(await screen.findByRole("button", { name: "Copy failed" })).toBeTruthy();
    await waitFor(() => expect(window.getSelection()?.toString()).toBe(ADDRESS));
  });
});

describe("on Settings", () => {
  it("is drawn under the auto-join rules for a connected Google calendar when the server sent an address", () => {
    settings(calendarOn);
    expect(tip()).toBeTruthy();
    expect(screen.getByText(ADDRESS)).toBeTruthy();
    const rules = document.querySelector(".cal-rules") as Element;
    const aside = document.querySelector(".invite-tip") as Element;
    expect(rules.compareDocumentPosition(aside) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("is not drawn when the server sent no address (null), though the field is there", () => {
    expect("bot_invite_email" in calendarOff).toBe(true);
    settings(calendarOff);
    expect(tip()).toBeNull();
    expect(screen.getByText("Join automatically")).toBeTruthy(); // the section itself is unchanged
  });

  it("is not drawn for an Outlook calendar: the tip is about Google Meet", () => {
    settings({ ...calendarOn, provider: "microsoft", account_email: "ada@outlook.example.test" });
    expect(tip()).toBeNull();
  });
});

describe("on a meeting's page", () => {
  it("is drawn while the Google Meet bot waits to be let in, beside Cancel", () => {
    meeting(waiting);
    expect(tip()).toBeTruthy();
    expect(screen.getByText(ADDRESS)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel and remove the bot" })).toBeTruthy();
  });

  it("is drawn when the bot was not let in, with the reason and Try again", () => {
    meeting(notLetIn);
    expect(screen.getByText(/The bot was not let into the meeting/)).toBeTruthy();
    expect(tip()).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy address" })).toBeTruthy();
  });

  it.each([
    ["a Zoom meeting that waits (the host admits Zoom guests)", zoom],
    ["a bot that is already in the call", inside],
    ["a server that is not set up for the tip", waitingOff],
  ])("is not drawn for %s", (_name, m) => {
    expect("bot_invite_email" in m).toBe(true);
    meeting(m);
    expect(tip()).toBeNull();
  });
});
