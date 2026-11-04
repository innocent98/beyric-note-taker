// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { join } from "node:path";
import { RecurringCards } from "@/components/app/recurring-cards";
import { ApiError } from "@/lib/api/client";
import type { RecurringState } from "@/lib/api/types";
import { REPO, read } from "./helpers";

// The Recurring cards on the Meetings screen, fed the bodies captured from the running app
// (docs/fe-captures/calendar-recurring/, cited in docs/fe-integration-guide-calendar-settings.md).

const mocks = vi.hoisted(() => ({ recurring: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { calendar: { recurring: mocks.recurring } } }));

const capture = (file: string) =>
  JSON.parse(read(join(REPO, "docs/fe-captures/calendar-recurring", file))).response.body as RecurringState;
const lagos = capture("10-series-lagos.json");
const empty = capture("05-not-connected.json");

beforeEach(() => {
  mocks.recurring.mockReset();
});

const show = async (answer: Promise<RecurringState> | RecurringState) => {
  mocks.recurring.mockReturnValue(Promise.resolve(answer));
  const view = render(<RecurringCards />);
  await waitFor(() => expect(mocks.recurring).toHaveBeenCalled());
  return view;
};
const cards = () => screen.getAllByRole("listitem");

describe("with recurring meetings", () => {
  it("draws the designer's heading and one card for each series, soonest first", async () => {
    await show(lagos);
    expect(await screen.findByRole("heading", { level: 2, name: "Recurring" })).toBeTruthy();
    expect(cards().map((c) => within(c).getByText(/^(Stand-up|Beyric|Vendor|Board)/).textContent)).toEqual([
      "Stand-up",
      "Beyric Friday review",
      "Beyric Monday planning",
      "Vendor sync",
      "Board call 🚀 週次",
    ]);
  });

  it("shows the name, the cadence and the Next chip exactly as the server wrote them", async () => {
    await show(lagos);
    const monday = (await screen.findByText("Beyric Monday planning")).closest("li")!;
    expect(within(monday).getByText("Weekly on Mondays, 14:00")).toBeTruthy();
    expect(within(monday).getByText("Next: Mon 9 Nov, 14:00")).toBeTruthy();
    const vendor = screen.getByText("Vendor sync").closest("li")!;
    expect(within(vendor).getByText("Recurring")).toBeTruthy(); // what the times cannot prove
    expect(within(vendor).getByText("Next: Tue 10 Nov, 10:00")).toBeTruthy();
  });

  it("round-trips emoji and non-Latin titles", async () => {
    await show(lagos);
    expect(await screen.findByText("Board call 🚀 週次")).toBeTruthy();
  });

  it("draws only the chip that is real: no unfinished tasks and no open questions", async () => {
    const { container } = await show(lagos);
    await screen.findByText("Beyric Monday planning");
    expect(container.textContent).not.toMatch(/unfinished|open question|task/i);
    expect(container.querySelectorAll(".rec-chip")).toHaveLength(lagos.series.length);
  });

  it("is not a link and not pressable: no chevron, no button, nothing to focus", async () => {
    const { container } = await show(lagos);
    await screen.findByText("Beyric Monday planning");
    expect(container.querySelector("a, button, [role=button], [role=link], [tabindex], svg")).toBeNull();
    for (const card of cards()) expect(card.getAttribute("onclick")).toBeNull();
  });

  it("asks for the texts in the browser's own time zone", async () => {
    await show(lagos);
    expect(mocks.recurring).toHaveBeenCalledWith(Intl.DateTimeFormat().resolvedOptions().timeZone);
  });
});

describe("with nothing to draw", () => {
  it("draws nothing at all when no calendar is connected or nothing recurs", async () => {
    const { container } = await show(empty);
    await waitFor(() => expect(mocks.recurring).toHaveBeenCalledTimes(1));
    expect(container.innerHTML).toBe("");
  });

  it("draws nothing while waiting for the answer", () => {
    mocks.recurring.mockReturnValue(new Promise(() => {}));
    const { container } = render(<RecurringCards />);
    expect(container.innerHTML).toBe("");
  });

  it.each([
    ["the calendar is off (404 calendar_unavailable)", new ApiError(404, "calendar_unavailable", "Google Calendar is not available.")],
    ["the zone is refused (422)", new ApiError(422, "invalid_timezone", "That time zone is not known.")],
    ["the server fails (500)", new ApiError(500, "internal_error", "Something went wrong.")],
    ["the network is down", new TypeError("Failed to fetch")],
  ])("draws nothing and does not break when %s", async (_name, failure) => {
    mocks.recurring.mockRejectedValue(failure);
    const { container } = render(<RecurringCards />);
    await waitFor(() => expect(mocks.recurring).toHaveBeenCalled());
    await Promise.resolve();
    expect(container.innerHTML).toBe("");
  });

  it("does not update after it was taken off the page", async () => {
    let finish: (state: RecurringState) => void = () => {};
    mocks.recurring.mockReturnValue(new Promise<RecurringState>((resolve) => (finish = resolve)));
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const { unmount, container } = render(<RecurringCards />);
    unmount();
    finish(lagos);
    await Promise.resolve();
    expect(errors).not.toHaveBeenCalled();
    expect(container.innerHTML).toBe("");
    errors.mockRestore();
  });
});
