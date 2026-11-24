// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsCalendar } from "@/components/app/settings-calendar";
import { ApiError } from "@/lib/api/client";
import type { CalendarState } from "@/lib/api/types";
import { skipBody, skipError } from "./helpers";

// "Skip one-on-one meetings" (LEAD-DESIGNED, 2026-11-24): the second box under "Join automatically",
// fed the bodies captured from the running app (docs/fe-captures/skip-one-on-ones/, cited in
// docs/fe-integration-guide-calendar-settings.md).

const mocks = vi.hoisted(() => ({ settings: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({
  api: { calendar: { get: vi.fn(), connect: vi.fn(), disconnect: vi.fn(), settings: mocks.settings } },
}));

const boxOff = skipBody<CalendarState>("05-state-default.json");
const afterTick = skipBody<CalendarState>("07-settings-skip-on.json");
const organizerKeepsBox = skipBody<CalendarState>("14-settings-rule-organizer-keeps-skip.json");
const noneKeepsBox = skipBody<CalendarState>("15-settings-rule-none-keeps-skip.json");
const afterUntick = skipBody<CalendarState>("17-settings-skip-off.json");

beforeEach(() => {
  mocks.settings.mockReset();
});
afterEach(cleanup);

const view = (initial: CalendarState) => render(<SettingsCalendar initial={initial} notice={null} navigate={vi.fn()} />);
const box = () => screen.getByRole("checkbox", { name: "Skip one-on-one meetings" }) as HTMLInputElement;
const OFF_HELP = "Off: meetings with only two people invited are joined like any other.";
const ON_HELP =
  "On: the note taker skips meetings with only two people invited. You can still switch one on from the Upcoming list.";

describe("the box 'Skip one-on-one meetings'", () => {
  it("is unticked for a person who never ticked it, with the help that says what off means", () => {
    view(boxOff);
    expect(boxOff.skip_one_on_ones).toBe(false);
    expect(box().checked).toBe(false);
    expect(box().disabled).toBe(false);
    expect(screen.getByText(OFF_HELP)).toBeTruthy();
    expect(box().getAttribute("aria-describedby")).toBe("skip-one-on-ones-help");
  });

  it("leaves the other box and the first rule's sentence as they were while it is off", () => {
    view(boxOff);
    expect(screen.getByRole("checkbox", { name: "Also join invites I haven't answered yet" })).toBeTruthy();
    expect(
      screen.getByText(
        "Every event on your calendar with a meeting link, except invites you haven't answered from outside your organisation.",
      ),
    ).toBeTruthy();
  });

  it("writes only the box when it is ticked, then shows the server's answer and says so in the rule sentence", async () => {
    mocks.settings.mockResolvedValue(afterTick);
    view(boxOff);
    await userEvent.click(box());
    expect(mocks.settings).toHaveBeenCalledTimes(1);
    expect(mocks.settings).toHaveBeenCalledWith({ skip_one_on_ones: true });
    await waitFor(() => expect(box().checked).toBe(true));
    expect(screen.getByText(ON_HELP)).toBeTruthy();
    // Every sentence stays true: the exception is named next to the other one.
    expect(
      screen.getByText(
        "Every event on your calendar with a meeting link, except invites you haven't answered from outside your organisation and meetings with only two people invited.",
      ),
    ).toBeTruthy();
  });

  it("shows the tick at once and holds both boxes until the server has answered", async () => {
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
    expect(mocks.settings).toHaveBeenCalledWith({ skip_one_on_ones: false });
    await waitFor(() => expect(box().checked).toBe(false));
    expect(screen.getByText(OFF_HELP)).toBeTruthy();
  });

  it("drops the tick and says so when the server refuses", async () => {
    const refused = skipError("21-settings-skip-not-a-boolean.json");
    mocks.settings.mockRejectedValue(new ApiError(refused.status, refused.code, refused.message));
    view(boxOff);
    await userEvent.click(box());
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Something went wrong. Please try again."));
    expect(box().checked).toBe(false);
  });

  it("stays usable under 'Only meetings I organise' and names itself in that rule's sentence", () => {
    view(organizerKeepsBox);
    expect(box().checked).toBe(true);
    expect(box().disabled).toBe(false);
    expect(screen.getByText("Skip meetings other people invited you to. Also skip meetings with only two people invited.")).toBeTruthy();
  });

  it("is disabled, ticked as stored, and says where it applies under 'Only when I turn it on'", () => {
    view(noneKeepsBox);
    expect(box().checked).toBe(true);
    expect(box().disabled).toBe(true);
    expect(screen.getByText("Only used with All meetings with a video link or Only meetings I organise.")).toBeTruthy();
  });

  it("is not drawn while no calendar is connected", () => {
    const notConnected: CalendarState = { ...boxOff, connected: false, state: "not_connected", account_email: null, provider: null };
    view(notConnected);
    expect(screen.queryByRole("checkbox", { name: /one-on-one/ })).toBeNull();
  });
});
