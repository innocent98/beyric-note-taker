// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentView } from "@/components/app/document-view";
import { ApiError } from "@/lib/api/client";
import type { DocumentDraft } from "@/lib/api/types";
import { recipesBody } from "./helpers";

// A board opened from a draft: the document page hands it to the board screen (slice S6), which
// edits in place. The draft is a real capture (docs/fe-captures/recipes/board/04-get-board.json;
// the model faked there).

const mocks = vi.hoisted(() => ({ get: vi.fn(), edit: vi.fn(), export: vi.fn(), push: vi.fn(), save: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/lib/api/browser", () => ({
  api: { documents: { get: mocks.get, export: mocks.export }, board: { edit: mocks.edit } },
}));
vi.mock("@/lib/save-file", () => ({ saveBlob: mocks.save }));

const board = () => recipesBody<DocumentDraft>("board/04-get-board.json");
const moved = () => recipesBody<DocumentDraft>("board/05-move-card.json");

beforeEach(() => Object.values(mocks).forEach((fn) => fn.mockReset()));
afterEach(() => cleanup());

const col = (name: string) => screen.getByRole("region", { name });
const cardsIn = (name: string) =>
  within(col(name))
    .queryAllByTestId("board-card")
    .map((c) => within(c).getByTestId("card-text").textContent);

describe("a board on the document page", () => {
  it("shows its columns and cards, and the page's Download and Discard, but no block Edit", () => {
    render(<DocumentView initial={board()} />);
    expect(screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"))).toEqual([
      "Pricing and start",
      "Next steps",
      "Open questions",
      "Parked",
    ]);
    expect(cardsIn("Next steps")).toEqual(["Send the plan by Friday", "Confirm the kick-off call on [need: call date]"]);
    expect(screen.getByRole("heading", { level: 1, name: "Onboarding board" })).toBeTruthy();
    expect(screen.getByText("Board")).toBeTruthy();
    expect(screen.getByText("Download")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Discard" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull(); // the board edits in place
  });

  it("changes through the board route with the draft's updated_at, and shows the answer", async () => {
    mocks.edit.mockResolvedValue(moved());
    render(<DocumentView initial={board()} />);
    const menu = within(col("Pricing and start")).getByRole("combobox", { name: /move.*setup fee/i });
    await userEvent.selectOptions(menu, "Parked");
    await userEvent.click(within(col("Pricing and start")).getByRole("button", { name: /^move$/i }));
    expect(mocks.edit).toHaveBeenCalledWith(board().id, board().updated_at, { op: "move_card", card: 2, to_column: 3, index: 0 });
    await waitFor(() => expect(cardsIn("Parked")).toEqual(["Setup fee of 120,000 naira"]));
    expect(cardsIn("Pricing and start")).toEqual(["Onboarding is 50,000 naira", "Start on 2026-12-01"]);
  });

  it("sends the NEW revision on the next change", async () => {
    mocks.edit.mockResolvedValueOnce(moved()).mockResolvedValue(moved());
    render(<DocumentView initial={board()} />);
    await userEvent.selectOptions(within(col("Pricing and start")).getByRole("combobox", { name: /move.*setup fee/i }), "Parked");
    await userEvent.click(within(col("Pricing and start")).getByRole("button", { name: /^move$/i }));
    await waitFor(() => expect(cardsIn("Parked")).toHaveLength(1));
    await userEvent.selectOptions(within(col("Next steps")).getAllByRole("combobox")[0], "Parked");
    await userEvent.click(within(col("Next steps")).getByRole("button", { name: /^move$/i }));
    expect(mocks.edit.mock.calls[1][1]).toBe(moved().updated_at);
  });

  it("fetches the draft again when the board changed in another tab", async () => {
    mocks.edit.mockRejectedValue(new ApiError(409, "stale_board", "The board changed since you opened it. It has been reloaded."));
    mocks.get.mockResolvedValue(moved());
    render(<DocumentView initial={board()} />);
    await userEvent.selectOptions(within(col("Pricing and start")).getByRole("combobox", { name: /move.*start on/i }), "Parked");
    await userEvent.click(within(col("Pricing and start")).getByRole("button", { name: /^move$/i }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/changed since you opened it/i);
    expect(mocks.get).toHaveBeenCalledWith(board().id);
    await waitFor(() => expect(cardsIn("Parked")).toEqual(["Setup fee of 120,000 naira"])); // what the page now holds
  });

  it("downloads a board as PDF through the export route", async () => {
    mocks.export.mockResolvedValue({ blob: new Blob(["pdf"]), filename: "Onboarding board.pdf" });
    render(<DocumentView initial={board()} />);
    await userEvent.click(screen.getByText("Download"));
    await userEvent.click(screen.getByRole("button", { name: /PDF/ }));
    expect(mocks.export).toHaveBeenCalledWith(board().id, "pdf", 0);
    await waitFor(() => expect(mocks.save).toHaveBeenCalled());
  });
});
