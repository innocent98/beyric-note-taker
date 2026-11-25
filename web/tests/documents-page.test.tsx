// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DocumentsView } from "@/components/app/documents-view";
import { MeetingView } from "@/components/app/meeting-view";
import { DOCUMENT_PAGE } from "@/lib/documents";
import type { DocumentList, DocumentRow, Meeting, Template } from "@/lib/api/types";
import { captured, recipesBody } from "./helpers";

// The Documents page (my drafts, and the drafts teammates shared with me) and the "Documents from this meeting"
// card of the meeting page: lead-designed, the designer's files draw none of it. The row shape is the real
// capture docs/fe-captures/recipes/12-list.json (made before sharing existed) with the sharing fields the
// contract added (`access`, `owner_name`, `shared_with`) set by hand here: NOT a captured body.

const mocks = vi.hoisted(() => ({ list: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    documents: { list: mocks.list },
    milestones: { forMeeting: () => Promise.resolve({ milestones: [] }), confirm: vi.fn() },
    series: { list: vi.fn().mockResolvedValue({ timezone: "UTC", series: [] }), create: vi.fn() },
    meetings: { get: vi.fn() },
    recipes: { list: vi.fn() },
    brief: { get: vi.fn() },
    teams: { list: vi.fn().mockResolvedValue({ teams: [], can_create_more: 0 }) },
  },
}));

const base = recipesBody<DocumentList>("12-list.json").documents[0] as DocumentRow;
const row = (id: number, extra: Partial<DocumentRow> = {}): DocumentRow => ({
  ...base,
  id,
  title: `Draft ${id}`,
  access: "owner",
  owner_name: null,
  shared_with: [],
  ...extra,
});
const list = (...rows: DocumentRow[]): DocumentList => ({ documents: rows });
const many = (from: number, count: number, extra: Partial<DocumentRow> = {}) => Array.from({ length: count }, (_, i) => row(from - i, extra));

beforeEach(() => {
  mocks.list.mockReset();
  mocks.push.mockReset();
});
afterEach(cleanup);

describe("the Documents page", () => {
  it("lists my drafts: title, kind, status and date, each a link to the draft", () => {
    render(<DocumentsView initialTab="mine" initial={list(row(3, { status: "writing", output_type: "table" }), row(2, { status: "failed" }), row(1))} />);
    const rows = within(screen.getByRole("list", { name: "Your drafts" })).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    const link = within(rows[0]).getByRole("link") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/documents/3");
    expect(within(rows[0]).getByText("Draft 3")).toBeTruthy();
    expect(within(rows[0]).getByText("Table")).toBeTruthy();
    expect(within(rows[0]).getByText("Writing")).toBeTruthy();
    expect(within(rows[1]).getByText("Failed")).toBeTruthy();
    expect(within(rows[2]).getByText("Ready")).toBeTruthy();
    expect(within(rows[2]).getByText("Email")).toBeTruthy();
    expect(rows[2].querySelector("time")?.getAttribute("datetime")).toBe(base.created_at);
  });

  it("says who a draft of mine is shared with", () => {
    render(<DocumentsView initialTab="mine" initial={list(row(1, { shared_with: [{ id: 7, name: "Sales team" }] }))} />);
    expect(screen.getByText("Shared with Sales team")).toBeTruthy();
  });

  it("has two tabs, Mine and Shared with me, and opens on the one asked for", () => {
    render(<DocumentsView initialTab="shared" initial={list(row(5, { access: "shared", owner_name: "Ada Obi" }))} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Mine", "Shared with me"]);
    expect(screen.getByRole("tab", { name: "Shared with me" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("list", { name: "Drafts shared with you" })).toBeTruthy();
  });

  it("asks for the shared drafts when that tab is first opened, and shows who shared each", async () => {
    let answer: (value: DocumentList) => void = () => {};
    mocks.list.mockReturnValue(new Promise<DocumentList>((resolve) => (answer = resolve)));
    render(<DocumentsView initialTab="mine" initial={list(row(1))} />);
    await userEvent.click(screen.getByRole("tab", { name: "Shared with me" }));
    expect(screen.getByRole("status").textContent).toContain("Loading drafts");
    answer(list(row(9, { access: "shared", owner_name: "Ada Obi" }), row(8, { access: "shared", owner_name: "Tunde Bello" })));
    const shared = await screen.findByRole("list", { name: "Drafts shared with you" });
    expect(mocks.list).toHaveBeenCalledWith({ shared: true, limit: DOCUMENT_PAGE });
    expect(within(shared).getByText("Shared by Ada Obi")).toBeTruthy();
    expect(within(shared).getByText("Shared by Tunde Bello")).toBeTruthy();
    // back to Mine: already read, so no second request
    await userEvent.click(screen.getByRole("tab", { name: "Mine" }));
    expect(screen.getByText("Draft 1")).toBeTruthy();
    expect(mocks.list).toHaveBeenCalledTimes(1);
  });

  it("has a designed empty state for each tab, in plain words", async () => {
    mocks.list.mockResolvedValue(list());
    render(<DocumentsView initialTab="mine" initial={list()} />);
    expect(screen.getByText("No drafts yet.")).toBeTruthy();
    expect(screen.getByText(/press Make to write a follow-up/)).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "Shared with me" }));
    expect(await screen.findByText("Nothing has been shared with you.")).toBeTruthy();
    expect(screen.getByText(/shares a draft with your team, you can read it here/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
  });

  it("says the list could not be loaded and tries again", async () => {
    mocks.list.mockRejectedValueOnce(new TypeError("offline")).mockResolvedValueOnce(list(row(4, { access: "shared", owner_name: "Ada Obi" })));
    render(<DocumentsView initialTab="mine" initial={list(row(1))} />);
    await userEvent.click(screen.getByRole("tab", { name: "Shared with me" }));
    expect((await screen.findByRole("alert")).textContent).toContain("We could not load the drafts shared with you.");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Draft 4")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("loads more with the smallest id shown, appends the page, and stops when a short page comes back", async () => {
    mocks.list.mockResolvedValueOnce(list(...many(30, 5)));
    render(<DocumentsView initialTab="mine" initial={list(...many(DOCUMENT_PAGE + 29, DOCUMENT_PAGE))} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(DOCUMENT_PAGE);
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(mocks.list).toHaveBeenCalledWith({ mine: true, limit: DOCUMENT_PAGE, beforeId: 30 });
    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(DOCUMENT_PAGE + 5));
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
  });

  it("keeps what is shown when loading more fails, and says so", async () => {
    mocks.list.mockRejectedValueOnce(new TypeError("offline"));
    render(<DocumentsView initialTab="mine" initial={list(...many(DOCUMENT_PAGE, DOCUMENT_PAGE))} />);
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("Could not load more drafts. Try again.")).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(DOCUMENT_PAGE);
    expect(screen.getByRole("button", { name: "Load more" })).toBeTruthy();
  });

  it("does not offer Load more on a page shorter than a full one", () => {
    render(<DocumentsView initialTab="mine" initial={list(...many(5, 5))} />);
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
  });
});

describe("Documents from this meeting", () => {
  const templates: Template[] = [{ id: 1, name: "General", instructions: "g" }];
  const ready = () => captured<Meeting>("47-meeting-detail-ready.json");

  it("lists the drafts made from the meeting, each a link, and says who shared a teammate's", async () => {
    mocks.list.mockResolvedValue(list(row(12, { title: "Proposal for Daniel" }), row(11, { title: "Weekly recap", access: "shared", owner_name: "Ada Obi" })));
    const m = ready();
    render(<MeetingView initial={m} templates={templates} />);
    const heading = await screen.findByRole("heading", { name: "Documents from this meeting" });
    expect(mocks.list).toHaveBeenCalledWith({ meetingId: m.id, limit: 10 });
    const card = heading.parentElement as HTMLElement;
    expect((within(card).getByRole("link", { name: "Proposal for Daniel" }) as HTMLAnchorElement).getAttribute("href")).toBe("/documents/12");
    expect((within(card).getByRole("link", { name: "Weekly recap" }) as HTMLAnchorElement).getAttribute("href")).toBe("/documents/11");
    expect(within(card).getByText("Shared by Ada Obi")).toBeTruthy();
  });

  it("draws nothing when there are none, or when the list cannot be read", async () => {
    mocks.list.mockResolvedValueOnce(list());
    const { unmount } = render(<MeetingView initial={ready()} templates={templates} />);
    await waitFor(() => expect(mocks.list).toHaveBeenCalled());
    expect(screen.queryByRole("heading", { name: "Documents from this meeting" })).toBeNull();
    unmount();
    mocks.list.mockRejectedValueOnce(new TypeError("offline"));
    render(<MeetingView initial={ready()} templates={templates} />);
    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("heading", { name: "Documents from this meeting" })).toBeNull();
  });

  it("is not asked for while the meeting is not ready", () => {
    render(<MeetingView initial={captured<Meeting>("43-meeting-detail-processing.json")} templates={templates} />);
    expect(mocks.list).not.toHaveBeenCalled();
  });
});
