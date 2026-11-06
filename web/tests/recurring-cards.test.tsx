// @vitest-environment jsdom
import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { join } from "node:path";
import { RecurringCards } from "@/components/app/recurring-cards";
import { ApiError } from "@/lib/api/client";
import type { SeriesList } from "@/lib/api/types";
import { REPO, read } from "./helpers";

// The Recurring cards on the Meetings screen, fed the bodies captured from the running app
// (docs/fe-captures/series-w8/, cited in docs/fe-integration-guide-series.md).

const mocks = vi.hoisted(() => ({ list: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { series: { list: mocks.list } } }));

const capture = (file: string) =>
  JSON.parse(read(join(REPO, "docs/fe-captures/series-w8", file))).response.body as SeriesList;
const owner = capture("08-cards-owner.json");
const teammate = capture("09-cards-teammate.json");
const outsider = capture("10-cards-outsider.json");

beforeEach(() => {
  mocks.list.mockReset();
});

const show = async (answer: SeriesList) => {
  mocks.list.mockReturnValue(Promise.resolve(answer));
  const view = render(<RecurringCards />);
  await waitFor(() => expect(mocks.list).toHaveBeenCalled());
  return view;
};
const cards = () => screen.getAllByRole("listitem");

describe("with recurring meetings", () => {
  it("draws the designer's heading and one card for each series, in the server's order", async () => {
    await show(owner);
    expect(await screen.findByRole("heading", { level: 2, name: "Recurring" })).toBeTruthy();
    expect(cards().map((c) => within(c).getByText(/^Beyric/).textContent)).toEqual([
      "Beyric Friday review",
      "Beyric Monday planning",
    ]);
  });

  it("shows the name, the cadence and the three chips exactly as the server wrote them", async () => {
    await show(owner);
    const monday = (await screen.findByText("Beyric Monday planning")).closest("li")!;
    expect(within(monday).getByText("Weekly on Mondays, 14:00")).toBeTruthy();
    expect(within(monday).getByText("Next: Mon 9 Nov, 14:00")).toBeTruthy();
    expect(within(monday).getByText("4 unfinished tasks")).toBeTruthy();
    expect(within(monday).getByText("2 open questions")).toBeTruthy();
    const friday = screen.getByText("Beyric Friday review").closest("li")!;
    expect(within(friday).getByText("Recurring")).toBeTruthy(); // what the times cannot prove
  });

  it("counts what the viewer may read: a teammate sees fewer unfinished tasks", async () => {
    await show(teammate);
    const monday = (await screen.findByText("Beyric Monday planning")).closest("li")!;
    expect(within(monday).getByText("3 unfinished tasks")).toBeTruthy();
  });

  it("makes each card a link to its Series page", async () => {
    await show(owner);
    const monday = (await screen.findByText("Beyric Monday planning")).closest("a")!;
    expect(monday.getAttribute("href")).toBe("/series/1");
    expect(screen.getByText("Beyric Friday review").closest("a")!.getAttribute("href")).toBe("/series/3");
  });

  it("leaves out the Next chip when no next meeting is known", async () => {
    await show({ ...owner, series: [{ ...owner.series[0], next_starts_at: null, next_text: null }] });
    await screen.findByText("Beyric Friday review");
    expect(screen.queryByText(/^Next:/)).toBeNull();
  });

  it("uses singular words for one", async () => {
    await show({ ...owner, series: [{ ...owner.series[0], unfinished_tasks: 1, open_questions: 1 }] });
    expect(await screen.findByText("1 unfinished task")).toBeTruthy();
    expect(screen.getByText("1 open question")).toBeTruthy();
  });
});

describe("with nothing to draw", () => {
  it("draws nothing at all when the team has no recurring meeting", async () => {
    const { container } = await show(outsider);
    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(1));
    expect(container.innerHTML).toBe("");
  });

  it("draws nothing while waiting for the answer", () => {
    mocks.list.mockReturnValue(new Promise(() => {}));
    const { container } = render(<RecurringCards />);
    expect(container.innerHTML).toBe("");
  });

  it.each([
    ["the server fails (500)", new ApiError(500, "internal_error", "Something went wrong.")],
    ["the network is down", new TypeError("Failed to fetch")],
  ])("draws nothing and does not break when %s", async (_name, failure) => {
    mocks.list.mockRejectedValue(failure);
    const { container } = render(<RecurringCards />);
    await waitFor(() => expect(mocks.list).toHaveBeenCalled());
    await Promise.resolve();
    expect(container.innerHTML).toBe("");
  });

  it("does not update after it was taken off the page", async () => {
    let finish: (state: SeriesList) => void = () => {};
    mocks.list.mockReturnValue(new Promise<SeriesList>((resolve) => (finish = resolve)));
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const { unmount, container } = render(<RecurringCards />);
    unmount();
    finish(owner);
    await Promise.resolve();
    expect(errors).not.toHaveBeenCalled();
    expect(container.innerHTML).toBe("");
    errors.mockRestore();
  });
});
