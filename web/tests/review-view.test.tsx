// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReviewView } from "@/components/app/review-view";
import { ApiError, GATEWAY_MESSAGE } from "@/lib/api/client";
import type { Meeting, ReviewItem, ReviewList, ReviewTermResult } from "@/lib/api/types";
import { captured, workspaceBody, workspaceError } from "./helpers";

// The Review screen is fed the bodies captured from the running API
// (docs/fe-captures/ai-workspace-w1/, cited in docs/fe-integration-guide-ai-workspace.md section 4).

const mocks = vi.hoisted(() => ({
  refresh: vi.fn(),
  check: vi.fn(),
  uncheck: vi.fn(),
  addToVocabulary: vi.fn(),
  get: vi.fn(),
  regenerate: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: mocks.refresh }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    review: { check: mocks.check, uncheck: mocks.uncheck, addToVocabulary: mocks.addToVocabulary },
    meetings: { get: mocks.get, regenerate: mocks.regenerate },
  },
}));

const open = workspaceBody<ReviewList>("29-review-open.json");
const checkedOne = workspaceBody<ReviewItem>("31-review-check.json");
const added = workspaceBody<ReviewTermResult>("36-review-vocabulary.json");
// Item 1 is the one in the checked list, so it is not in the open list beside it.
const openWithout1 = open.items.filter((i) => i.id !== 1);
const noChecked: ReviewList = { items: [], open_count: 3, truncated: false };
const withChecked: ReviewList = { items: [{ ...checkedOne }], open_count: 2, truncated: false };
// A ready meeting whose notes can be rewritten (the owner-app capture) for the rewrite offer.
const readyMeeting = captured<Meeting>("47-meeting-detail-ready.json");

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
});
afterEach(cleanup);

const metrika = open.items.find((i) => i.heard_as === "Metrika")!;
const card = (text: string) => screen.getByText(text).closest("section") as HTMLElement;

describe("open items", () => {
  it("lists the items with their meeting and day, and the spelling box only where heard_as is set", () => {
    render(<ReviewView open={open} checked={noChecked} />);
    expect(screen.getByRole("heading", { level: 1, name: "Review" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Mark as checked" })).toHaveLength(3);
    expect(screen.getAllByText(/Heard as/)).toHaveLength(1); // the contract number has heard_as null
    const box = card(metrika.text);
    expect(within(box).getByText("Metrika").tagName).toBe("STRONG");
    expect(box.textContent).toContain("Google Meet nat-aaaa-bbb · Sat 31 Oct 2026");
    const number = card(open.items.find((i) => i.heard_as === null && i.id === 2)!.text);
    expect(within(number).queryByRole("button", { name: "Add to vocabulary" })).toBeNull();
  });

  it("links Open transcript to the transcript tab of the meeting", () => {
    render(<ReviewView open={open} checked={noChecked} />);
    const link = within(card(metrika.text)).getByRole("link", { name: "Open transcript" });
    expect(link.getAttribute("href")).toBe("/meetings/1?tab=transcript");
  });

  it("marks an item as checked: it leaves the open list, joins Checked, and the sidebar is refreshed", async () => {
    mocks.check.mockResolvedValue(checkedOne);
    render(<ReviewView open={open} checked={noChecked} />);
    expect(screen.queryByRole("button", { name: /^Checked/ })).toBeNull();
    await userEvent.click(within(card(metrika.text)).getByRole("button", { name: "Mark as checked" }));
    expect(mocks.check).toHaveBeenCalledWith(1);
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Mark as checked" })).toHaveLength(2));
    expect(screen.getByRole("button", { name: "Checked (1)" })).toBeTruthy();
    expect(mocks.refresh).toHaveBeenCalled();
  });

  it("shows the API's words and keeps the item when checking fails", async () => {
    mocks.check.mockRejectedValue(new ApiError(502, "gateway_error", GATEWAY_MESSAGE));
    render(<ReviewView open={open} checked={noChecked} />);
    await userEvent.click(within(card(metrika.text)).getByRole("button", { name: "Mark as checked" }));
    expect((await within(card(metrika.text)).findByRole("alert")).textContent).toBe(GATEWAY_MESSAGE);
    expect(screen.getAllByRole("button", { name: "Mark as checked" })).toHaveLength(3);
  });

  it("says the item is gone, and drops it, when the API answers the captured 404", async () => {
    const { status, code, message } = workspaceError("40-review-check-foreign.json");
    mocks.check.mockRejectedValue(new ApiError(status, code, message));
    render(<ReviewView open={open} checked={noChecked} />);
    await userEvent.click(within(card(metrika.text)).getByRole("button", { name: "Mark as checked" }));
    expect(await screen.findByText(/no longer there/)).toBeTruthy();
    expect(screen.queryByText(metrika.text)).toBeNull();
  });
});

describe("checked items", () => {
  it("keeps Checked closed until it is opened, then offers Undo", async () => {
    render(<ReviewView open={{ ...open, items: openWithout1 }} checked={withChecked} />);
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    const toggle = screen.getByRole("button", { name: "Checked (1)" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    await userEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button", { name: "Undo" })).toBeTruthy();
  });

  it("moves an item back to the open list on Undo", async () => {
    mocks.uncheck.mockResolvedValue(workspaceBody<ReviewItem>("34-review-uncheck.json"));
    render(<ReviewView open={{ ...open, items: openWithout1 }} checked={withChecked} />);
    await userEvent.click(screen.getByRole("button", { name: "Checked (1)" }));
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(mocks.uncheck).toHaveBeenCalledWith(1);
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Mark as checked" })).toHaveLength(3));
    expect(screen.queryByRole("button", { name: /^Checked/ })).toBeNull();
    expect(mocks.refresh).toHaveBeenCalled();
  });
});

describe("add to vocabulary", () => {
  it("adds the typed spelling, moves the item to Checked and offers to rewrite the notes", async () => {
    mocks.addToVocabulary.mockResolvedValue(added);
    render(<ReviewView open={open} checked={noChecked} />);
    await userEvent.type(screen.getByLabelText("Correct spelling of Metrika"), "  Metrika ");
    await userEvent.click(screen.getByRole("button", { name: "Add to vocabulary" }));
    expect(mocks.addToVocabulary).toHaveBeenCalledWith(1, "Metrika");
    const offer = await screen.findByRole("status");
    expect(offer.textContent).toContain("Added Metrika to your vocabulary.");
    expect(offer.textContent).toContain("still have the old spelling");
    expect(offer.textContent).toContain("Decisions you removed and items you checked may come back");
    expect(screen.getByRole("button", { name: "Checked (1)" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Mark as checked" })).toHaveLength(2);
  });

  it("does not call the API for an empty term, and says so in the API's own words", async () => {
    render(<ReviewView open={open} checked={noChecked} />);
    await userEvent.click(screen.getByRole("button", { name: "Add to vocabulary" }));
    expect(screen.getByText(workspaceError("35-review-vocabulary-invalid.json").message)).toBeTruthy();
    expect(mocks.addToVocabulary).not.toHaveBeenCalled();
  });

  it("shows the API's message under the box when it refuses the term (400), and the item stays open", async () => {
    const { status, code, message } = workspaceError("35-review-vocabulary-invalid.json");
    mocks.addToVocabulary.mockRejectedValue(new ApiError(status, code, message));
    render(<ReviewView open={open} checked={noChecked} />);
    await userEvent.type(screen.getByLabelText("Correct spelling of Metrika"), "!!");
    await userEvent.click(screen.getByRole("button", { name: "Add to vocabulary" }));
    expect(await screen.findByText("Type a term to add.")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Mark as checked" })).toHaveLength(3);
  });

  it("treats the captured 409 as done: the item is checked and the rewrite is still offered", async () => {
    const { status, code, message } = workspaceError("38-review-vocabulary-duplicate.json");
    mocks.addToVocabulary.mockRejectedValue(new ApiError(status, code, message));
    render(<ReviewView open={open} checked={noChecked} />);
    await userEvent.type(screen.getByLabelText("Correct spelling of Metrika"), "Metrika");
    await userEvent.click(screen.getByRole("button", { name: "Add to vocabulary" }));
    const offer = await screen.findByRole("status");
    expect(offer.textContent).toContain("is already in your vocabulary, so nothing was added.");
    expect(screen.getByRole("button", { name: "Checked (1)" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Mark as checked" })).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Rewrite the notes with the corrected spelling" })).toBeTruthy();
  });

  it("empties the box once the term is added, so Undo does not bring the old text back", async () => {
    mocks.addToVocabulary.mockResolvedValue(added);
    mocks.uncheck.mockResolvedValue(workspaceBody<ReviewItem>("34-review-uncheck.json"));
    render(<ReviewView open={open} checked={noChecked} />);
    await userEvent.type(screen.getByLabelText("Correct spelling of Metrika"), "Metrika");
    await userEvent.click(screen.getByRole("button", { name: "Add to vocabulary" }));
    await userEvent.click(await screen.findByRole("button", { name: "Checked (1)" }));
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect((await screen.findByLabelText("Correct spelling of Metrika") as HTMLInputElement).value).toBe("");
  });

  it("saves with Enter in the box", async () => {
    mocks.addToVocabulary.mockResolvedValue(added);
    render(<ReviewView open={open} checked={noChecked} />);
    await userEvent.type(screen.getByLabelText("Correct spelling of Metrika"), "Metrika{Enter}");
    expect(mocks.addToVocabulary).toHaveBeenCalledWith(1, "Metrika");
  });
});

describe("rewrite offer", () => {
  async function addMetrika() {
    mocks.addToVocabulary.mockResolvedValue(added);
    render(<ReviewView open={open} checked={noChecked} />);
    await userEvent.type(screen.getByLabelText("Correct spelling of Metrika"), "Metrika");
    await userEvent.click(screen.getByRole("button", { name: "Add to vocabulary" }));
    await screen.findByRole("status");
  }
  const rewriteButton = () => screen.getByRole("button", { name: "Rewrite the notes with the corrected spelling" });

  it("rewrites with the meeting's own template, shows it running, then links to the notes", async () => {
    let finish: (m: Meeting) => void = () => undefined;
    mocks.get.mockResolvedValue(readyMeeting);
    mocks.regenerate.mockReturnValue(new Promise<Meeting>((resolve) => (finish = resolve)));
    await addMetrika();
    await userEvent.click(rewriteButton());
    expect(await screen.findByText(/Rewriting the notes. This can take a few minutes/)).toBeTruthy();
    expect(mocks.get).toHaveBeenCalledWith(1);
    expect(mocks.regenerate).toHaveBeenCalledWith(1, readyMeeting.template_id);
    finish(readyMeeting);
    expect(await screen.findByText(/were rewritten/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open notes" }).getAttribute("href")).toBe("/meetings/1");
  });

  it("leaves the offer away on Not now", async () => {
    await addMetrika();
    await userEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(screen.queryByRole("status")).toBeNull();
    expect(mocks.regenerate).not.toHaveBeenCalled();
  });

  it("says so, without rewriting, when the meeting cannot be rewritten now", async () => {
    mocks.get.mockResolvedValue({ ...readyMeeting, can_regenerate: false });
    await addMetrika();
    await userEvent.click(rewriteButton());
    expect(await screen.findByText("These notes cannot be rewritten right now.")).toBeTruthy();
    expect(mocks.regenerate).not.toHaveBeenCalled();
    expect(rewriteButton()).toBeTruthy(); // can be tried again
  });

  it("says the rewrite may still finish when the edge gives up on the slow call", async () => {
    mocks.get.mockResolvedValue(readyMeeting);
    mocks.regenerate.mockRejectedValue(new ApiError(504, "gateway_error", GATEWAY_MESSAGE));
    await addMetrika();
    await userEvent.click(rewriteButton());
    expect(await screen.findByText(/may still finish: open the notes in a few minutes/)).toBeTruthy();
  });
});

describe("empty and long lists", () => {
  it("says there is nothing to review yet when no item exists, open or checked", () => {
    render(<ReviewView open={{ items: [], open_count: 0, truncated: false }} checked={{ items: [], open_count: 0, truncated: false }} />);
    expect(screen.getByText("Nothing to review yet.")).toBeTruthy();
    expect(screen.getByText(/Meetings from before this feature were not scanned/)).toBeTruthy();
  });

  it("says All caught up when every item has been checked", () => {
    render(<ReviewView open={{ items: [], open_count: 0, truncated: false }} checked={withChecked} />);
    expect(screen.getByText("All caught up")).toBeTruthy();
    expect(screen.getByText("New items appear here after each meeting.")).toBeTruthy();
    expect(screen.queryByText("Nothing to review yet.")).toBeNull();
  });

  it("says when only the newest 200 items are shown", () => {
    render(<ReviewView open={{ ...open, truncated: true }} checked={noChecked} />);
    expect(screen.getByText(/Showing the 200 newest items/)).toBeTruthy();
  });
});
