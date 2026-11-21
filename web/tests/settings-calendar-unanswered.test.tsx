// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsCalendar } from "@/components/app/settings-calendar";
import { ApiError } from "@/lib/api/client";
import type { CalendarState } from "@/lib/api/types";
import { unansweredBody, unansweredError } from "./helpers";

// "Also join invites I haven't answered yet" (LEAD-DESIGNED, 2026-11-21): the box under the first
// rule, fed the bodies captured from the running app (docs/fe-captures/join-unanswered/, cited in
// docs/fe-integration-guide-calendar-settings.md).

const mocks = vi.hoisted(() => ({ settings: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({
  api: { calendar: { get: vi.fn(), connect: vi.fn(), disconnect: vi.fn(), settings: mocks.settings } },
}));

const boxOff = unansweredBody<CalendarState>("05-state-default.json");
const afterTick = unansweredBody<CalendarState>("07-settings-box-on.json");
const organizerKeepsBox = unansweredBody<CalendarState>("10-settings-rule-organizer-keeps-box.json");
const afterUntick = unansweredBody<CalendarState>("12-settings-box-off.json");

beforeEach(() => {
  mocks.settings.mockReset();
});
afterEach(cleanup);

const view = (initial: CalendarState) => render(<SettingsCalendar initial={initial} notice={null} navigate={vi.fn()} />);
const box = () => screen.getByRole("checkbox", { name: "Also join invites I haven't answered yet" }) as HTMLInputElement;
const OFF_HELP = "Off: the note taker waits until you accept an invite from someone outside your organisation.";

describe("the box under 'All meetings with a video link'", () => {
  it("is unticked for a person who never ticked it, with the help that says what off means", () => {
    view(boxOff);
    expect(box().checked).toBe(false);
    expect(box().disabled).toBe(false);
    expect(screen.getByText(OFF_HELP)).toBeTruthy();
    expect(box().getAttribute("aria-describedby")).toBe("join-unanswered-help");
  });

  it("names the exception in the first rule's sentence while the box is off", () => {
    view(boxOff);
    expect(
      screen.getByText(
        "Every event on your calendar with a meeting link, except invites you haven't answered from outside your organisation.",
      ),
    ).toBeTruthy();
  });

  it("writes only the box when it is ticked, then shows the server's answer", async () => {
    mocks.settings.mockResolvedValue(afterTick);
    view(boxOff);
    await userEvent.click(box());
    expect(mocks.settings).toHaveBeenCalledTimes(1);
    expect(mocks.settings).toHaveBeenCalledWith({ join_unanswered: true });
    await waitFor(() => expect(box().checked).toBe(true));
    expect(screen.getByText("On: the note taker joins them without waiting for you to accept.")).toBeTruthy();
    expect(screen.getByText("Every event on your calendar with a meeting link.")).toBeTruthy();
  });

  it("shows the tick at once and holds the box until the server has answered", async () => {
    let answer: (state: CalendarState) => void = () => {};
    mocks.settings.mockReturnValue(new Promise<CalendarState>((resolve) => (answer = resolve)));
    view(boxOff);
    await userEvent.click(box());
    expect(box().checked).toBe(true);
    expect(box().disabled).toBe(true);
    answer(afterTick);
    await waitFor(() => expect(box().disabled).toBe(false));
  });

  it("writes false when a ticked box is cleared", async () => {
    mocks.settings.mockResolvedValue(afterUntick);
    view(afterTick);
    expect(box().checked).toBe(true);
    await userEvent.click(box());
    expect(mocks.settings).toHaveBeenCalledWith({ join_unanswered: false });
    await waitFor(() => expect(box().checked).toBe(false));
  });

  it("drops the tick and says so when the server refuses", async () => {
    const refused = unansweredError("15-settings-box-not-a-boolean.json");
    mocks.settings.mockRejectedValue(new ApiError(refused.status, refused.code, refused.message));
    view(boxOff);
    await userEvent.click(box());
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Something went wrong. Please try again."));
    expect(box().checked).toBe(false);
  });

  it("is disabled, ticked as stored, and says where it applies under another rule", () => {
    view(organizerKeepsBox);
    expect(box().checked).toBe(true);
    expect(box().disabled).toBe(true);
    expect(screen.getByText("Only used with All meetings with a video link.")).toBeTruthy();
  });

  it("is not drawn while no calendar is connected", () => {
    const notConnected: CalendarState = { ...boxOff, connected: false, state: "not_connected", account_email: null, provider: null };
    view(notConnected);
    expect(screen.queryByRole("checkbox", { name: /haven't answered/ })).toBeNull();
  });
});
