// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingsCalendar } from "@/components/app/meetings-calendar";
import { ApiError } from "@/lib/api/client";
import type { CalendarState, NotRecordedList, SendNowResult } from "@/lib/api/types";
import { sendNowBody, sendNowError } from "./helpers";

const mocks = vi.hoisted(() => ({ sendNow: vi.fn(), override: vi.fn(), get: vi.fn(), refresh: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({
  api: { calendar: { sendNow: mocks.sendNow, override: mocks.override, get: mocks.get } },
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: mocks.refresh }) }));

// Not recorded and Send now, fed the bodies captured from the running app
// (docs/fe-captures/send-now/, scripts/capture_send_now.py).
const list = sendNowBody<NotRecordedList>("11-list.json");
const upcoming = sendNowBody<CalendarState>("12-calendar-upcoming.json");
const sent = sendNowBody<SendNowResult>("19-send-now.json");
const busy = sendNowError("13-send-now-busy.json");
const ended = sendNowError("25-send-now-ended.json");
const endedList = sendNowBody<NotRecordedList>("24-list-with-ended.json");

// The capture was made on 2026-11-08 at about 21:00 UTC; the page is read at 21:15 UTC that day.
const NOW = new Date(new Date(list.items[1].starts_at).getTime() + 17 * 60000);

beforeEach(() => {
  for (const mock of Object.values(mocks)) mock.mockReset();
});
afterEach(cleanup);

const show = (state: CalendarState | null, items = list.items) =>
  render(<MeetingsCalendar state={state} notRecorded={items} timeZone="UTC" now={NOW} />);
const notRecordedRows = () =>
  within(screen.getByRole("list", { name: "Meetings not recorded" })).getAllByRole("listitem");

describe("the Not recorded list", () => {
  it("draws a card with a chip and a row per meeting: day, time, title, platform, duration, the server's reason", () => {
    show(null);
    expect(screen.getByRole("heading", { level: 2, name: "Not recorded" })).toBeTruthy();
    expect(screen.getByText("Last 7 days")).toBeTruthy();
    const rows = notRecordedRows();
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Design review")).toBeTruthy();
    expect(within(rows[0]).getByText("Google Meet")).toBeTruthy();
    expect(within(rows[0]).getByText("1 hr")).toBeTruthy();
    expect(within(rows[0]).getByText("The note taker was busy with other meetings.")).toBeTruthy();
    expect(within(rows[1]).getByText("The note taker could not join before the 15 minutes were up.")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Send now" })).toHaveLength(2);
  });

  it("draws nothing at all when nothing was missed", () => {
    const { container } = show(null, []);
    expect(container.querySelector("section")).toBeNull();
  });

  it("offers no Send now for a meeting that is over, but still says it was not recorded", () => {
    show(null, endedList.items);
    const over = notRecordedRows().find((row) => within(row).queryByText("Google Meet eee-eeee-eee"));
    expect(over).toBeTruthy();
    expect(within(over!).queryByRole("button", { name: "Send now" })).toBeNull();
    expect(within(over!).getByText(/could not join before the 15 minutes/)).toBeTruthy();
  });

  it("never shows a state name or a code", () => {
    const { container } = show(null);
    for (const word of ["no_capacity", "failed", "not_recorded", "skipped", "join_id"]) {
      expect(container.textContent).not.toContain(word);
    }
  });
});

describe("pressing Send now", () => {
  it("sends the row's join id, shows the server's toast, drops the row and reads the page again", async () => {
    mocks.sendNow.mockResolvedValue(sent);
    show(null);
    const row = notRecordedRows()[1];
    await userEvent.click(within(row).getByRole("button", { name: "Send now" }));
    expect(mocks.sendNow).toHaveBeenCalledWith(list.items[1].join_id);
    await waitFor(() => expect(screen.getAllByText(sent.status_text).length).toBeGreaterThan(0));
    expect(notRecordedRows()).toHaveLength(1);
    expect(screen.queryByText("Weekly planning")).toBeNull();
    expect(mocks.refresh).toHaveBeenCalledTimes(1);
  });

  it("disables the button while the call is out and ignores a second press", async () => {
    let finish: (value: SendNowResult) => void = () => {};
    mocks.sendNow.mockReturnValue(new Promise<SendNowResult>((resolve) => (finish = resolve)));
    show(null);
    const row = notRecordedRows()[1];
    await userEvent.click(within(row).getByRole("button", { name: "Send now" }));
    const pending = within(row).getByRole("button", { name: "Sending…" });
    expect((pending as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(pending);
    expect(mocks.sendNow).toHaveBeenCalledTimes(1);
    finish(sent);
    await waitFor(() => expect(notRecordedRows()).toHaveLength(1));
  });

  it("keeps the row and the button after a refusal and says why in the server's words", async () => {
    mocks.sendNow.mockRejectedValue(new ApiError(busy.status, busy.code, busy.message));
    show(null);
    const row = notRecordedRows()[0];
    await userEvent.click(within(row).getByRole("button", { name: "Send now" }));
    const alert = await within(row).findByRole("alert");
    expect(alert.textContent).toBe("The note taker is busy right now. Try again in a few minutes.");
    expect(within(row).getByRole("button", { name: "Send now" })).toBeTruthy();
    expect(notRecordedRows()).toHaveLength(2);
    expect(mocks.refresh).not.toHaveBeenCalled();
  });

  it("clears the message when the person tries again", async () => {
    mocks.sendNow
      .mockRejectedValueOnce(new ApiError(busy.status, busy.code, busy.message))
      .mockResolvedValueOnce(sent);
    show(null);
    const row = notRecordedRows()[0];
    await userEvent.click(within(row).getByRole("button", { name: "Send now" }));
    await within(row).findByRole("alert");
    await userEvent.click(within(row).getByRole("button", { name: "Send now" }));
    await waitFor(() => expect(notRecordedRows()).toHaveLength(1));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("reads the page again when the answer says the meeting has changed behind it (over)", async () => {
    mocks.sendNow.mockRejectedValue(new ApiError(ended.status, ended.code, ended.message));
    show(null);
    const row = notRecordedRows()[1];
    await userEvent.click(within(row).getByRole("button", { name: "Send now" }));
    expect((await within(row).findByRole("alert")).textContent).toBe(ended.message);
    expect(mocks.refresh).toHaveBeenCalledTimes(1);
  });

  it("says the meeting is gone, not the developer's text, for a 404", async () => {
    mocks.sendNow.mockRejectedValue(new ApiError(404, "not_found", "No such meeting"));
    show(null);
    const row = notRecordedRows()[1];
    await userEvent.click(within(row).getByRole("button", { name: "Send now" }));
    expect((await within(row).findByRole("alert")).textContent).toBe("This meeting is no longer on your list.");
  });
});

describe("the Upcoming rows", () => {
  const rowOf = (title: string) =>
    within(screen.getByRole("list", { name: "Upcoming meetings" }))
      .getAllByRole("listitem")
      .find((row) => within(row).queryByText(title))!;

  it("says a missed meeting was not recorded and offers Send now on it, and on no other row", () => {
    show(upcoming, []);
    const missed = rowOf("Weekly planning");
    expect(within(missed).getByText("Not recorded: The note taker could not join before the 15 minutes were up.")).toBeTruthy();
    expect(within(missed).getByRole("button", { name: "Send now" })).toBeTruthy();
    expect(within(missed).getByRole("switch")).toBeTruthy();
    for (const title of ["Customer call", "Retro"]) {
      expect(within(rowOf(title)).queryByRole("button", { name: "Send now" })).toBeNull();
      expect(within(rowOf(title)).queryByText(/Not recorded/)).toBeNull();
    }
  });

  it("sends from the row with the join id, not the event id", async () => {
    mocks.sendNow.mockResolvedValue(sent);
    show(upcoming, []);
    const event = upcoming.upcoming.find((e) => e.title === "Weekly planning")!;
    expect(event.join_id).not.toBeNull();
    await userEvent.click(within(rowOf("Weekly planning")).getByRole("button", { name: "Send now" }));
    expect(mocks.sendNow).toHaveBeenCalledWith(event.join_id);
    await waitFor(() => expect(mocks.refresh).toHaveBeenCalled());
    expect(within(rowOf("Weekly planning")).queryByRole("button", { name: "Send now" })).toBeNull();
  });
});
