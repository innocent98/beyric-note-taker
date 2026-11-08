// @vitest-environment jsdom
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AskGoneToast } from "@/components/app/ask-gone-toast";
import { AskView } from "@/components/app/ask-view";
import { AppShell } from "@/components/app/shell";
import { MeetingView } from "@/components/app/meeting-view";
import { ApiError, NETWORK_MESSAGE } from "@/lib/api/client";
import type { AskAnswer, AskCite, AskScope, Meeting } from "@/lib/api/types";
import { ASK_CHIP_FLAG, citeHref, citeLabel, parseLine } from "@/lib/ask";
import { captured, read, REPO } from "./helpers";
import { join } from "node:path";

// Ask Beyric: the page's states, the source chips' words and links, the meeting page's transcript
// highlight and the toast for a meeting that is gone. The API is faked to the spec's contract
// (docs/superpowers/specs/2026-11-08-designer-v2-features-design.md 1.7).

const mocks = vi.hoisted(() => ({ ask: vi.fn(), pathname: "/meetings/5" }));
vi.mock("next/navigation", () => ({ usePathname: () => mocks.pathname, useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    ask: { ask: mocks.ask },
    milestones: { forMeeting: () => Promise.resolve({ milestones: [] }), confirm: vi.fn() },
    series: { list: vi.fn().mockResolvedValue({ timezone: "UTC", series: [] }), create: vi.fn() },
    meetings: { get: vi.fn() },
  },
}));

const scope: AskScope = {
  meetings: 12,
  decisions: 31,
  tasks: 44,
  suggestions: ["What did we decide about pricing?", "What did I commit to last week?", "What did Daniel say?", "When is the launch?", "Which decisions were made on Monday?"],
};

const mine: AskCite = { meeting_id: 1, title: "Beyric Monday planning", date_label: "Mon 2 Nov", owner_name: null, shared: false, target: "notes", line: null };
const spoken: AskCite = { ...mine, meeting_id: 8, title: "Zoom 88205679674", target: "transcript", line: 12 };
const theirs: AskCite = { ...mine, meeting_id: 9, title: "Zoom 99205679674", owner_name: "Daniel", shared: true, target: "transcript", line: 3 };

const answer: AskAnswer = {
  kind: "answer",
  lead: "No pricing model has been chosen yet.",
  points: [
    { text: "Customer interviews will happen once onboarding is stable.", cites: [spoken, theirs] },
    { text: "It is logged as a proposed decision.", cites: [mine] },
  ],
};

beforeEach(() => {
  mocks.ask.mockReset();
  sessionStorage.clear();
});
afterEach(cleanup);

const box = () => screen.getByRole("textbox", { name: "Your question" });
const show = (s: AskScope | null = scope) => render(<AskView scope={s} initials="AT" />);
const type = (text: string) => userEvent.type(box(), text);

describe("Ask Beyric page", () => {
  it("draws the title, the subtitle, the box with its placeholder, the five chips and the scope line, and no thread", () => {
    show();
    expect(screen.getByRole("heading", { level: 1, name: "Ask Beyric" })).toBeTruthy();
    expect(screen.getByText("Ask about anything said, decided or assigned in your meetings. Every answer links to the moment it came from.")).toBeTruthy();
    expect((box() as HTMLInputElement).placeholder).toBe("e.g. What did we decide about pricing?");
    expect(screen.getByRole("button", { name: "Ask" })).toBeTruthy();
    for (const text of scope.suggestions) expect(screen.getByRole("button", { name: text })).toBeTruthy();
    expect(screen.getByText("Searches 12 meetings, 31 decisions and 44 tasks")).toBeTruthy();
    expect(screen.queryByText("Nothing to search yet. Meetings appear here after they end.")).toBeNull();
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("uses singular words for a count of one", () => {
    show({ ...scope, meetings: 1, decisions: 1, tasks: 1 });
    expect(screen.getByText("Searches 1 meeting, 1 decision and 1 task")).toBeTruthy();
  });

  it("with no ready meetings keeps the drawn scope line with zeros and adds one grey line (lead-designed)", () => {
    show({ ...scope, meetings: 0, decisions: 0, tasks: 0 });
    expect(screen.getByText("Searches 0 meetings, 0 decisions and 0 tasks")).toBeTruthy();
    expect(screen.getByText("Nothing to search yet. Meetings appear here after they end.")).toBeTruthy();
  });

  it("leaves out the chips and the scope line when the scope could not be loaded, and still asks", async () => {
    mocks.ask.mockResolvedValue(answer);
    show(null);
    expect(screen.queryByText(/^Searches/)).toBeNull();
    expect(screen.queryByRole("button", { name: "When is the launch?" })).toBeNull();
    await type("pricing{Enter}");
    expect(await screen.findByText("No pricing model has been chosen yet.")).toBeTruthy();
  });

  it("asks on Enter, trims the question, empties the box and shows 'Searching your meetings...' with the person's initials", async () => {
    let finish: (a: AskAnswer) => void = () => undefined;
    mocks.ask.mockReturnValue(new Promise<AskAnswer>((resolve) => (finish = resolve)));
    show();
    await type("  What did we decide about pricing?  {Enter}");
    expect(mocks.ask).toHaveBeenCalledWith("What did we decide about pricing?");
    expect((box() as HTMLInputElement).value).toBe("");
    expect(screen.getByRole("heading", { level: 2, name: "What did we decide about pricing?" })).toBeTruthy();
    expect(screen.getByText("AT")).toBeTruthy();
    expect(screen.getByText("Searching your meetings...")).toBeTruthy();
    await act(async () => finish(answer));
    expect(screen.queryByText("Searching your meetings...")).toBeNull();
    expect(screen.getByText("No pricing model has been chosen yet.")).toBeTruthy();
  });

  it("asks with the Ask button, and ignores an empty box", async () => {
    mocks.ask.mockResolvedValue(answer);
    show();
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await type("   ");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(mocks.ask).not.toHaveBeenCalled();
    await type("pricing");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(mocks.ask).toHaveBeenCalledWith("pricing");
  });

  it("asks a suggestion chip's own words at once", async () => {
    mocks.ask.mockResolvedValue(answer);
    show();
    await userEvent.click(screen.getByRole("button", { name: "When is the launch?" }));
    expect(mocks.ask).toHaveBeenCalledWith("When is the launch?");
    expect(await screen.findByText("No pricing model has been chosen yet.")).toBeTruthy();
  });

  it("limits the box to 300 characters, the API's bound", () => {
    show();
    expect((box() as HTMLInputElement).maxLength).toBe(300);
  });

  it("shows the lead, the points, the source chips and 'Answers can be wrong. Check the sources.'", async () => {
    mocks.ask.mockResolvedValue(answer);
    show();
    await type("pricing{Enter}");
    expect(await screen.findByText("Customer interviews will happen once onboarding is stable.")).toBeTruthy();
    expect(screen.getByText("It is logged as a proposed decision.")).toBeTruthy();
    expect(screen.getByText("Answers can be wrong. Check the sources.")).toBeTruthy();
    expect(screen.queryByText("Try asking about a person, a project, a decision or a date.")).toBeNull();
  });

  it("lists the newest question first", async () => {
    mocks.ask.mockResolvedValue(answer);
    show();
    await type("first{Enter}");
    await screen.findByText("No pricing model has been chosen yet.");
    await type("second{Enter}");
    const asked = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(asked).toEqual(["second", "first"]);
  });

  it("says 'I could not find that in your meetings.' with the hint, and no caveat line, for a 'none' answer", async () => {
    mocks.ask.mockResolvedValue({ kind: "none", lead: "I could not find that in your meetings.", points: [] });
    show();
    await type("zebra{Enter}");
    expect(await screen.findByText("I could not find that in your meetings.")).toBeTruthy();
    expect(screen.getByText("Try asking about a person, a project, a decision or a date.")).toBeTruthy();
    expect(screen.queryByText("Answers can be wrong. Check the sources.")).toBeNull();
  });

  it("draws a tasks answer like any other (lead and chips)", async () => {
    mocks.ask.mockResolvedValue({ kind: "tasks", lead: "You made 1 commitment.", points: [{ text: "Finish payment checkout. Due Fri 6 Nov.", cites: [mine] }] });
    show();
    await type("my tasks{Enter}");
    expect(await screen.findByText("You made 1 commitment.")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Beyric Monday planning · Mon 2 Nov/ })).toBeTruthy();
  });

  it("draws a task point with no cites as text alone (the API may send cites: [])", async () => {
    mocks.ask.mockResolvedValue({ kind: "tasks", lead: "You have 1 task.", points: [{ text: "Finish checkout.", cites: [] }] });
    show();
    await type("my tasks{Enter}");
    expect(await screen.findByText("Finish checkout.")).toBeTruthy();
    expect(document.querySelector(".ask-cites")).toBeNull();
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });

  it("shows text from the API as plain text, never as markup", async () => {
    mocks.ask.mockResolvedValue({ kind: "answer", lead: "<b>bold</b> **x**", points: [{ text: "<img src=x onerror=alert(1)>", cites: [mine] }] });
    show();
    await type("x{Enter}");
    expect(await screen.findByText("<b>bold</b> **x**")).toBeTruthy();
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector(".ask-lead b")).toBeNull();
  });
});

describe("Ask Beyric failures (lead-designed)", () => {
  it("shows 'Beyric could not answer that.' with Try again for a failed call, and asks the same question again", async () => {
    mocks.ask.mockRejectedValueOnce(new ApiError(502, "ask_failed", "Beyric could not answer that. Try again."));
    mocks.ask.mockResolvedValueOnce(answer);
    show();
    await type("pricing{Enter}");
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Beyric could not answer that.")).toBeTruthy();
    expect(screen.queryByText("I could not find that in your meetings.")).toBeNull();
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(mocks.ask).toHaveBeenCalledTimes(2);
    expect(mocks.ask).toHaveBeenLastCalledWith("pricing");
    expect(await screen.findByText("No pricing model has been chosen yet.")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    // one card, not two
    expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(1);
  });

  it("treats a lost connection like a failed call", async () => {
    mocks.ask.mockRejectedValue(new ApiError(0, "network_error", NETWORK_MESSAGE));
    show();
    await type("pricing{Enter}");
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Beyric could not answer that.")).toBeTruthy();
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("shows the cap message exactly as the API words it, with no button", async () => {
    mocks.ask.mockRejectedValue(new ApiError(429, "ask_limit", "You have asked 30 questions today. Try again tomorrow."));
    show();
    await type("pricing{Enter}");
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("You have asked 30 questions today. Try again tomorrow.")).toBeTruthy();
    expect(within(alert).queryByRole("button")).toBeNull();
    expect(screen.queryByText("Beyric could not answer that.")).toBeNull();
  });

  it("shows the per-minute brake's message with Try again (ask_rate is not the daily cap)", async () => {
    mocks.ask.mockRejectedValueOnce(new ApiError(429, "ask_rate", "Slow down a little and try again in a minute."));
    mocks.ask.mockResolvedValueOnce(answer);
    show();
    await type("pricing{Enter}");
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Slow down a little and try again in a minute.")).toBeTruthy();
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No pricing model has been chosen yet.")).toBeTruthy();
  });

  it("keeps an earlier answer on screen when a later question fails", async () => {
    mocks.ask.mockResolvedValueOnce(answer);
    mocks.ask.mockRejectedValueOnce(new ApiError(502, "ask_failed", "x"));
    show();
    await type("one{Enter}");
    await screen.findByText("No pricing model has been chosen yet.");
    await type("two{Enter}");
    await screen.findByRole("alert");
    expect(screen.getByText("No pricing model has been chosen yet.")).toBeTruthy();
  });
});

// The backend's captured responses (docs/fe-captures/ask-beyric/, from the running app): the page is fed the real bodies.
const capture = (file: string) => JSON.parse(read(join(REPO, "docs/fe-captures/ask-beyric", file))).response as { status: number; body: never };

describe("the backend's captured responses", () => {
  it("draws the captured scope and a captured transcript answer with its chip link", async () => {
    mocks.ask.mockResolvedValue(capture("15-ask-transcript-line.json").body);
    show(capture("10-scope-ada.json").body);
    expect(screen.getByText("Searches 2 meetings, 1 decision and 2 tasks")).toBeTruthy();
    expect(screen.getByRole("button", { name: "What did Daniel Okoro say?" })).toBeTruthy();
    await type("pricing{Enter}");
    const chip = await screen.findByRole("link", { name: /Pricing review · Mon 2 Nov/ });
    expect(chip.getAttribute("href")).toBe("/meetings/1?tab=transcript&line=1");
  });

  it("names the owner on a captured teammate chip", async () => {
    mocks.ask.mockResolvedValue(capture("16-ask-shared-teammate-meeting.json").body);
    show();
    await type("x{Enter}");
    const chip = await screen.findByRole("link", { name: /Design sync/ });
    expect(chip.textContent).toBe("Design sync · Tue 3 Nov · Dan Okoro");
    expect(chip.getAttribute("href")).toBe("/meetings/2");
  });

  it("shows a captured tasks answer with no points as its lead alone, not as 'could not find'", async () => {
    mocks.ask.mockResolvedValue(capture("25-ask-tasks-none.json").body);
    show();
    await type("my tasks{Enter}");
    expect(await screen.findByText("You have no open tasks assigned to you.")).toBeTruthy();
    expect(screen.queryByText("Try asking about a person, a project, a decision or a date.")).toBeNull();
  });

  it("tells the two captured 429s apart: the cap has no button, the per-minute brake has Try again", async () => {
    const cap = capture("22-ask-cap-reached.json");
    const rate = capture("31-ask-rate-limited.json");
    const err = (c: { status: number; body: { error: { code: string; message: string } } }) => new ApiError(c.status, c.body.error.code, c.body.error.message);
    mocks.ask.mockRejectedValueOnce(err(cap as never));
    show();
    await type("a{Enter}");
    const first = await screen.findByRole("alert");
    expect(within(first).getByText("You have asked 5 questions today. Try again tomorrow.")).toBeTruthy();
    expect(within(first).queryByRole("button")).toBeNull();
    mocks.ask.mockRejectedValueOnce(err(rate as never));
    await type("b{Enter}");
    const alerts = await screen.findAllByRole("alert");
    expect(within(alerts[0]).getByText("Slow down a little and try again in a minute.")).toBeTruthy();
    expect(within(alerts[0]).getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});

describe("source chips", () => {
  it("label '{title} · {date}' and add ' · {owner}' for a teammate's meeting", () => {
    expect(citeLabel(mine)).toBe("Beyric Monday planning · Mon 2 Nov");
    expect(citeLabel(theirs)).toBe("Zoom 99205679674 · Mon 2 Nov · Daniel");
  });

  it("link to the notes, or to the transcript row", () => {
    expect(citeHref(mine)).toBe("/meetings/1");
    expect(citeHref(spoken)).toBe("/meetings/8?tab=transcript&line=12");
    expect(citeHref({ ...spoken, line: null })).toBe("/meetings/8?tab=transcript");
  });

  it("are links on the page, with the video icon and no time anywhere", async () => {
    mocks.ask.mockResolvedValue(answer);
    show();
    await type("pricing{Enter}");
    await screen.findByText("It is logged as a proposed decision.");
    const hrefs = screen.getAllByRole("link").map((a) => [a.textContent, a.getAttribute("href")]);
    expect(hrefs).toEqual([
      ["Zoom 88205679674 · Mon 2 Nov", "/meetings/8?tab=transcript&line=12"],
      ["Zoom 99205679674 · Mon 2 Nov · Daniel", "/meetings/9?tab=transcript&line=3"],
      ["Beyric Monday planning · Mon 2 Nov", "/meetings/1"],
    ]);
    expect(document.body.textContent).not.toMatch(/\b\d{2}:\d{2}\b/);
  });

  it("remember the chip's meeting for the not-found toast", async () => {
    mocks.ask.mockResolvedValue(answer);
    show();
    await type("pricing{Enter}");
    const chip = await screen.findByRole("link", { name: /Zoom 88205679674/ });
    chip.addEventListener("click", (event) => event.preventDefault());
    await userEvent.click(chip);
    expect(sessionStorage.getItem(ASK_CHIP_FLAG)).toBe("/meetings/8");
  });
});

describe("a chip to a meeting that is gone", () => {
  it("says 'That meeting is no longer available' on the 404 page, once", async () => {
    sessionStorage.setItem(ASK_CHIP_FLAG, "/meetings/5");
    mocks.pathname = "/meetings/5";
    const { unmount } = render(<AskGoneToast />);
    expect(await screen.findByText("That meeting is no longer available")).toBeTruthy();
    expect(sessionStorage.getItem(ASK_CHIP_FLAG)).toBeNull();
    unmount();
    render(<AskGoneToast />);
    expect(screen.queryByText("That meeting is no longer available")).toBeNull();
  });

  it("says nothing when the 404 is some other page than the chip's, and clears the flag", () => {
    sessionStorage.setItem(ASK_CHIP_FLAG, "/meetings/5");
    mocks.pathname = "/meetings/77";
    render(<AskGoneToast />);
    expect(screen.queryByText("That meeting is no longer available")).toBeNull();
    expect(sessionStorage.getItem(ASK_CHIP_FLAG)).toBeNull();
  });

  it("says nothing on a 404 nobody linked to", () => {
    mocks.pathname = "/meetings/5";
    render(<AskGoneToast />);
    expect(screen.queryByText("That meeting is no longer available")).toBeNull();
  });
});

describe("?line= parameter", () => {
  it("reads a 1-based row number and nothing else", () => {
    expect(parseLine("12")).toBe(12);
    expect(parseLine(["3", "4"])).toBe(3);
    for (const bad of [undefined, "", "0", "-1", "1.5", "abc", "12abc", "1234567", " 3"]) expect(parseLine(bad)).toBeUndefined();
  });
});

describe("transcript highlight on the meeting page", () => {
  // The capture's transcript has two rows: "[Ada Example]: ..." and "[Ben Sample]: ...".
  const meeting = () => captured<Meeting>("47-meeting-detail-ready.json");
  const scrollTo = vi.fn();
  const rowOf = (name: string) => screen.getByText(name).closest(".transcript-line") as HTMLElement;

  beforeEach(() => {
    vi.useFakeTimers();
    scrollTo.mockReset();
    window.scrollTo = scrollTo as unknown as typeof window.scrollTo;
  });
  afterEach(() => vi.useRealTimers());

  const open = (props: { initialTab?: "notes" | "transcript"; initialLine?: number }, m: Meeting = meeting()) =>
    render(<MeetingView initial={m} templates={[]} {...props} />);

  it("opens the Transcript tab, gives row N the highlight, and scrolls to it after 300 ms with a 140 px offset, smoothly", () => {
    open({ initialTab: "transcript", initialLine: 2 });
    expect(screen.getByRole("tab", { name: "Transcript" }).getAttribute("aria-selected")).toBe("true");
    expect(rowOf("Ben Sample").className).toContain("transcript-line-hl");
    expect(rowOf("Ada Example").className).not.toContain("transcript-line-hl");
    expect(rowOf("Ben Sample").id).toBe("tl-2");
    const row = document.getElementById("tl-2") as HTMLElement;
    row.getBoundingClientRect = () => ({ top: 500 }) as DOMRect;
    Object.defineProperty(window, "scrollY", { value: 100, configurable: true });
    act(() => void vi.advanceTimersByTime(299));
    expect(scrollTo).not.toHaveBeenCalled();
    act(() => void vi.advanceTimersByTime(1));
    expect(scrollTo).toHaveBeenCalledWith({ top: 500 + 100 - 140, behavior: "smooth" });
  });

  it("shows no time on any row", () => {
    open({ initialTab: "transcript", initialLine: 1 });
    expect(document.querySelector(".transcript-body")?.textContent).not.toMatch(/\d{1,2}:\d{2}/);
  });

  it("opens the tab with no highlight and no scroll when the row is out of range", () => {
    open({ initialTab: "transcript", initialLine: 3 });
    expect(screen.getByRole("tab", { name: "Transcript" }).getAttribute("aria-selected")).toBe("true");
    expect(document.querySelector(".transcript-line-hl")).toBeNull();
    act(() => void vi.advanceTimersByTime(1000));
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it("opens the tab with no highlight when there is no line (the Review screen's link)", () => {
    open({ initialTab: "transcript" });
    expect(document.querySelector(".transcript-line-hl")).toBeNull();
    act(() => void vi.advanceTimersByTime(1000));
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it("ignores a line when the notes tab is the one asked for", () => {
    open({ initialTab: "notes", initialLine: 1 });
    expect(document.querySelector(".transcript-line")).toBeNull();
    act(() => void vi.advanceTimersByTime(1000));
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it("opens the notes with no highlight when the transcript is gone", () => {
    open({ initialTab: "transcript", initialLine: 1 }, { ...meeting(), transcript: null } as Meeting);
    expect(document.querySelector(".transcript-line-hl")).toBeNull();
    act(() => void vi.advanceTimersByTime(1000));
    expect(scrollTo).not.toHaveBeenCalled();
  });
});

describe("sidebar", () => {
  it("has Ask Beyric between Meetings and Tasks, enabled, and marks it current on its page", () => {
    const html = renderToStaticMarkup(<AppShell current="ask">x</AppShell>);
    const main = html.slice(html.indexOf('aria-label="Main"'));
    const order = ["Meetings", "Ask Beyric", "Tasks", "Projects", "Decisions", "Review", "Team"].map((label) => main.indexOf(`<span>${label}</span>`));
    expect(order.every((n) => n > 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    expect(html).toMatch(/href="\/ask"[^>]*aria-current="page"|aria-current="page"[^>]*href="\/ask"/);
    expect(html).not.toMatch(/href="\/ask"[^>]*aria-disabled/);
  });
});
