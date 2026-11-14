// @vitest-environment jsdom
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { join } from "node:path";
import { SeriesView } from "@/components/app/series-view";
import { ApiError } from "@/lib/api/client";
import type { SeriesPage } from "@/lib/api/types";
import { REPO, linkBody, read } from "./helpers";

// The Series page, fed the bodies captured from the running app (docs/fe-captures/series-w8/).

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  addAgenda: vi.fn(),
  removeAgenda: vi.fn(),
  sendAgenda: vi.fn(),
  update: vi.fn(),
  tasksList: vi.fn(),
}));
vi.mock("@/lib/api/browser", () => ({
  api: {
    series: {
      get: mocks.get,
      addAgenda: mocks.addAgenda,
      removeAgenda: mocks.removeAgenda,
      sendAgenda: mocks.sendAgenda,
    },
    questions: { update: mocks.update },
    tasks: { list: mocks.tasksList },
  },
}));

// The series-w8 captures are older than the two new blocks: they get the empty fields the API now always sends.
const capture = (file: string) =>
  ({
    topics: [],
    suggested_agenda: [],
    ...JSON.parse(read(join(REPO, "docs/fe-captures/series-w8", file))).response
      .body,
  }) as SeriesPage;
const owner = capture("11-page-owner.json");
const teammate = capture("12-page-teammate.json");
// The pages of docs/fe-captures/series-topics/ (the owner's, and a teammate who may not read one meeting).
const topicsCapture = (file: string) =>
  JSON.parse(read(join(REPO, "docs/fe-captures/series-topics", file))).response
    .body as SeriesPage;
const withTopics = topicsCapture("13-series-ada.json");
const teammateTopics = topicsCapture("14-series-dan.json");
const teammateAfterUnshare = topicsCapture("21-series-dan-after-unshare.json");

beforeEach(() => {
  cleanup();
  Object.values(mocks).forEach((fn) => fn.mockReset());
  mocks.tasksList.mockResolvedValue({ people: [] });
});

describe("the page the designer drew", () => {
  it("has the header, the next meeting, the button and the five blocks in order", () => {
    render(<SeriesView initial={owner} />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Beyric Monday planning" }),
    ).toBeTruthy();
    expect(
      screen.getByText("Weekly on Mondays, 14:00 · Google Meet"),
    ).toBeTruthy();
    expect(screen.getByText("Next meeting")).toBeTruthy();
    expect(screen.getByText("Mon 9 Nov, 14:00")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Send agenda to attendees" }),
    ).toBeTruthy();
    expect(
      screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent),
    ).toEqual([
      "Unfinished from last time",
      "Finished since last meeting",
      "Open questions carried over",
      "Other agenda items",
    ]);
    expect(screen.getByText("Past meetings")).toBeTruthy();
    expect(screen.getByText("Attendees")).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "All meetings" }).getAttribute("href"),
    ).toBe("/meetings");
  });

  it("draws the three score tiles, Overdue plain while nothing is late", () => {
    render(<SeriesView initial={owner} />);
    const tile = (label: string) =>
      screen.getByText(label).parentElement as HTMLElement;
    expect(
      within(tile("Done since last meeting")).getByText(
        String(owner.finished.length),
      ).className,
    ).toContain("sr-tile-done");
    expect(
      within(tile("Still open")).getByText(String(owner.unfinished.length)),
    ).toBeTruthy();
    const late = within(tile("Overdue")).getByText("0");
    expect(late.className).not.toContain("sr-tile-late");
    expect(
      screen
        .getByText("Done since last meeting")
        .closest(".sr-score")
        ?.nextElementSibling?.querySelector("h2")?.textContent,
    ).toBe("Unfinished from last time"); // the tiles sit above the first block
  });

  it("counts open tasks whose day has passed as Overdue, in red, and not as Still open", () => {
    const past = {
      ...owner,
      unfinished: owner.unfinished.map((t, i) =>
        i < 2 ? { ...t, due_date: "2020-01-02" } : t,
      ),
    };
    render(<SeriesView initial={past} />);
    const tile = (label: string) =>
      screen.getByText(label).parentElement as HTMLElement;
    const late = within(tile("Overdue")).getByText("2");
    expect(late.className).toContain("sr-tile-late");
    expect(
      within(tile("Still open")).getByText(String(owner.unfinished.length - 2)),
    ).toBeTruthy();
    expect(screen.getAllByText("Overdue, Thu 2 Jan").length).toBe(2); // the rows say it too
  });

  it("does not count a task due today or later as Overdue", () => {
    const future = {
      ...owner,
      unfinished: owner.unfinished.map((t) => ({
        ...t,
        due_date: "2999-01-01",
      })),
    };
    render(<SeriesView initial={future} />);
    expect(
      within(
        screen.getByText("Overdue").parentElement as HTMLElement,
      ).getByText("0"),
    ).toBeTruthy();
    expect(
      within(
        screen.getByText("Still open").parentElement as HTMLElement,
      ).getByText(String(owner.unfinished.length)),
    ).toBeTruthy();
  });

  it("draws every unfinished task with its status and owner, and 'Needs owner' when it has none", () => {
    render(<SeriesView initial={owner} />);
    expect(
      screen.getAllByText("Finish payment checkout").length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText("To do").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Needs owner").length).toBeGreaterThan(0);
    expect(screen.getAllByTitle("Dan Okoro").length).toBeGreaterThan(0);
  });

  it("draws a finished task with its tick and 'Done', and the count", () => {
    render(<SeriesView initial={owner} />);
    expect(screen.getByText("1 done")).toBeTruthy();
    expect(screen.getByText("Send the pricing draft")).toBeTruthy();
    expect(screen.getByText(/^Done (Today|[A-Z][a-z]{2} \d)/)).toBeTruthy();
    expect(
      screen.getByText(/Tasks from this series completed since/),
    ).toBeTruthy();
  });

  it("lists each carried question once with where it came from", () => {
    render(<SeriesView initial={owner} />);
    expect(
      screen.getAllByText("Which pricing model will the team choose?"),
    ).toHaveLength(1);
    expect(screen.getAllByText("From Beyric Monday planning").length).toBe(2);
    expect(
      screen.getAllByRole("button", { name: "Mark resolved" }),
    ).toHaveLength(2);
  });

  it("lists past meetings as links with their length and task count, and the attendees", () => {
    render(<SeriesView initial={owner} />);
    const links = screen
      .getAllByRole("link")
      .filter((a) => a.getAttribute("href")?.startsWith("/meetings/"));
    expect(links).toHaveLength(owner.past_meetings.length);
    expect(links[0].textContent).toMatch(/46 min/);
    expect(screen.getByText("Guest Visitor")).toBeTruthy();
  });

  it("a teammate sees a shorter page, and the empty lines read as the designer wrote them", () => {
    render(
      <SeriesView
        initial={{
          ...teammate,
          unfinished: [],
          finished: [],
          finished_since_text: null,
          questions: [],
          agenda: [],
          past_meetings: [],
          attendees: [],
        }}
      />,
    );
    expect(screen.getByText("Everything from last time is done.")).toBeTruthy();
    expect(
      screen.getByText("Nothing finished yet. Completed tasks will show here."),
    ).toBeTruthy();
    expect(screen.getByText("No open questions.")).toBeTruthy();
    expect(
      screen.getByText("No meetings recorded in this series yet."),
    ).toBeTruthy();
  });

  it("draws the sprint line under the next meeting when the server sends one, and nothing when it does not", () => {
    // Captures of the running app: Monday planning's next meeting opens Sprint 2; Friday review's opens none.
    const opens = {
      ...linkBody<SeriesPage>("35-page-planning.json"),
      topics: [],
      suggested_agenda: [],
    };
    const none = {
      ...linkBody<SeriesPage>("37-page-review.json"),
      topics: [],
      suggested_agenda: [],
    };
    const { unmount } = render(<SeriesView initial={opens} />);
    expect(opens.sprint_note).toBe("Opens Sprint 2");
    const line = screen.getByText("Opens Sprint 2");
    expect(line.previousElementSibling?.textContent).toBe(opens.next_text);
    unmount();
    render(<SeriesView initial={none} />);
    expect(none.sprint_note).toBeNull();
    expect(screen.getByText("Next meeting")).toBeTruthy();
    expect(screen.queryByText(/opens|closes/i)).toBeNull();
  });

  it("leaves out the Next meeting block when no date is known, and draws no sprint line", () => {
    render(
      <SeriesView
        initial={{ ...owner, next_text: null, next_starts_at: null }}
      />,
    );
    expect(screen.queryByText("Next meeting")).toBeNull();
    expect(screen.queryByText(/sprint/i)).toBeNull();
  });
});

describe("Mark resolved", () => {
  it("resolves the question and takes it off the list", async () => {
    mocks.update.mockResolvedValue({
      id: 3,
      text: "x",
      carriable: true,
      carry: true,
      resolved: true,
      can_update: true,
    });
    render(<SeriesView initial={owner} />);
    const first = owner.questions[0];
    await userEvent.click(
      screen.getAllByRole("button", { name: "Mark resolved" })[0],
    );
    expect(mocks.update).toHaveBeenCalledWith(first.id, { resolved: true });
    await waitFor(() => expect(screen.queryByText(first.text)).toBeNull());
  });

  it("is switched off for someone who may read but not change", () => {
    render(
      <SeriesView
        initial={{
          ...owner,
          questions: owner.questions.map((q) => ({ ...q, can_update: false })),
        }}
      />,
    );
    for (const button of screen.getAllByRole("button", {
      name: "Mark resolved",
    })) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("says so and keeps the question when the save fails", async () => {
    mocks.update.mockRejectedValue(
      new ApiError(
        403,
        "team_forbidden",
        "You can't do that here. Ask an owner or admin of your team.",
      ),
    );
    render(<SeriesView initial={owner} />);
    await userEvent.click(
      screen.getAllByRole("button", { name: "Mark resolved" })[0],
    );
    expect(await screen.findByText(/You can't do that here/)).toBeTruthy();
    expect(screen.getByText(owner.questions[0].text)).toBeTruthy();
  });
});

describe("the agenda", () => {
  it("adds a line on Add and on Enter, and clears the box", async () => {
    mocks.addAgenda.mockResolvedValue({
      id: 9,
      text: "Hiring update",
      can_remove: true,
    });
    render(<SeriesView initial={owner} />);
    const box = screen.getByLabelText("New agenda item");
    await userEvent.type(box, "Hiring update{Enter}");
    expect(mocks.addAgenda).toHaveBeenCalledWith(owner.id, "Hiring update");
    expect(await screen.findByText("Hiring update")).toBeTruthy();
    expect((box as HTMLInputElement).value).toBe("");
  });

  it("keeps Add off while the box is empty", () => {
    render(<SeriesView initial={owner} />);
    expect(
      (screen.getByRole("button", { name: "Add" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("shows the server's refusal for a web address", async () => {
    mocks.addAgenda.mockRejectedValue(
      new ApiError(
        422,
        "invalid_agenda_item",
        "Write the item without a web address.",
      ),
    );
    render(<SeriesView initial={owner} />);
    await userEvent.type(
      screen.getByLabelText("New agenda item"),
      "see https://x.test{Enter}",
    );
    expect(
      await screen.findByText("Write the item without a web address."),
    ).toBeTruthy();
  });

  it("removes a line the person may remove, and draws no remove button for the others", async () => {
    mocks.removeAgenda.mockResolvedValue(undefined);
    const initial = {
      ...owner,
      agenda: [
        { id: 1, text: "Mine", can_remove: true },
        { id: 2, text: "Theirs", can_remove: false },
      ],
    };
    render(<SeriesView initial={initial} />);
    expect(
      screen.queryByRole("button", { name: 'Remove "Theirs"' }),
    ).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: 'Remove "Mine"' }),
    );
    expect(mocks.removeAgenda).toHaveBeenCalledWith(owner.id, 1);
    await waitFor(() => expect(screen.queryByText("Mine")).toBeNull());
  });
});

describe("Send agenda to attendees", () => {
  it("sends, then flashes the number of attendees", async () => {
    mocks.sendAgenda.mockResolvedValue({ count: 1, names: ["Dan Okoro"] });
    render(<SeriesView initial={owner} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Send agenda to attendees" }),
    );
    expect(mocks.sendAgenda).toHaveBeenCalledWith(owner.id);
    expect(await screen.findByText("Agenda sent to 1 attendee")).toBeTruthy();
  });

  it("is switched off, with the reason, when nobody would receive it", () => {
    render(<SeriesView initial={{ ...owner, agenda_recipients: 0 }} />);
    const button = screen.getByRole("button", {
      name: "Send agenda to attendees",
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.title).toMatch(/nobody to send it to/);
  });

  it.each([
    ["no_attendees", 409, "There is nobody on your team to send it to yet."],
    [
      "agenda_rate_limited",
      429,
      "You have sent as many agendas as fit in a day. Try again tomorrow.",
    ],
  ])("shows the server's words for %s", async (code, status, message) => {
    mocks.sendAgenda.mockRejectedValue(new ApiError(status, code, message));
    render(<SeriesView initial={owner} />);
    await userEvent.click(
      screen.getByRole("button", { name: "Send agenda to attendees" }),
    );
    expect(await screen.findByText(message)).toBeTruthy();
  });
});

describe("opening a task", () => {
  it("opens the same drawer as the board", async () => {
    render(<SeriesView initial={owner} />);
    const row = screen
      .getAllByText("Finish payment checkout")[0]
      .closest("[role=button]") as HTMLElement;
    await userEvent.click(row);
    await waitFor(() => expect(mocks.tasksList).toHaveBeenCalled());
    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(
      within(screen.getByRole("dialog")).getAllByText("Finish payment checkout")
        .length,
    ).toBeGreaterThan(0);
  });
});

describe("Keeps coming up and Suggested agenda (captured pages)", () => {
  const h2s = () =>
    screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);

  it("draws both blocks between the score tiles and Unfinished, in the designer's words", () => {
    render(<SeriesView initial={withTopics} />);
    expect(h2s().slice(0, 3)).toEqual([
      "Keeps coming up",
      "Suggested agenda",
      "Unfinished from last time",
    ]);
    expect(
      screen.getByText(
        "Topics discussed in more than one meeting without a decision.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "Built from repeat topics, overdue work, open questions and tasks to confirm.",
      ),
    ).toBeTruthy();
    expect(
      screen
        .getByText("Done since last meeting")
        .closest(".sr-score")
        ?.nextElementSibling?.querySelector("h2")?.textContent,
    ).toBe("Keeps coming up");
  });

  it("draws each topic with the server's sub-line and the dates", () => {
    render(<SeriesView initial={withTopics} />);
    expect(screen.getByText("Pricing model")).toBeTruthy();
    expect(
      screen.getByText("Discussed in 3 meetings without a decision"),
    ).toBeTruthy();
    expect(
      screen.getByText("Mon 19 Oct, Mon 26 Oct and Mon 2 Nov 2026"),
    ).toBeTruthy();
    expect(screen.getByText("Compensation review")).toBeTruthy();
    expect(
      screen.getByText("Raised in 2 meetings, still no owner or date"),
    ).toBeTruthy();
    expect(screen.getByText("Mon 26 Oct and Mon 2 Nov 2026")).toBeTruthy();
  });

  it("numbers the suggested lines in the order received, each with Add to agenda", () => {
    render(<SeriesView initial={withTopics} />);
    const rows = Array.from(document.querySelectorAll(".sr-sug"));
    expect(rows.map((r) => r.querySelector(".sr-sug-n")?.textContent)).toEqual([
      "1",
      "2",
      "3",
      "4",
      "5",
    ]);
    expect(
      rows.map((r) => r.querySelector(".sr-sug-text")?.textContent),
    ).toEqual(withTopics.suggested_agenda.map((l) => l.text));
    expect(
      screen.getAllByRole("button", { name: "Add to agenda" }).length,
    ).toBe(5);
  });

  it("is absent when there is nothing: no heading, no spinner, no empty state", () => {
    render(<SeriesView initial={owner} />);
    expect(h2s()).not.toContain("Keeps coming up");
    expect(h2s()).not.toContain("Suggested agenda");
    expect(document.querySelector(".sr-topics")).toBeNull();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("draws what the server sent a teammate: the same topics, without the line he has no tasks for", () => {
    render(<SeriesView initial={teammateTopics} />);
    expect(screen.getByText("Pricing model")).toBeTruthy();
    expect(screen.getByText("Compensation review")).toBeTruthy();
    expect(screen.queryByText(/suggested task/)).toBeNull();
    expect(document.querySelectorAll(".sr-sug").length).toBe(4);
  });

  it("draws neither block, and nothing about why, when the server sent a teammate no topics and no lines", () => {
    render(<SeriesView initial={teammateAfterUnshare} />);
    expect(h2s()).not.toContain("Keeps coming up");
    expect(h2s()).not.toContain("Suggested agenda");
    expect(screen.queryByText(/hidden|unshared|private/i)).toBeNull();
  });

  it("Add to agenda posts the line's exact text and the row becomes Added; a second click removes it", async () => {
    const user = userEvent.setup();
    mocks.addAgenda.mockResolvedValue({
      id: 77,
      text: "Decide the pricing model",
      can_remove: true,
    });
    mocks.removeAgenda.mockResolvedValue(undefined);
    render(<SeriesView initial={withTopics} />);
    const row = () =>
      screen
        .getByText("Decide the pricing model", { selector: ".sr-sug-text" })
        .closest(".sr-sug") as HTMLElement;
    await user.click(
      within(row()).getByRole("button", { name: "Add to agenda" }),
    );
    expect(mocks.addAgenda).toHaveBeenCalledWith(
      withTopics.id,
      "Decide the pricing model",
    );
    const added = await within(row()).findByRole("button", { name: "Added" });
    expect(added.className).toContain("sr-sug-btn-on");
    expect(added.getAttribute("aria-pressed")).toBe("true");
    // the new item is in "Other agenda items"
    expect(document.querySelector(".sr-agenda-text")?.textContent).toBe(
      "Decide the pricing model",
    );
    await user.click(added);
    expect(mocks.removeAgenda).toHaveBeenCalledWith(withTopics.id, 77);
    await within(row()).findByRole("button", { name: "Add to agenda" });
    expect(document.querySelector(".sr-agenda-text")).toBeNull();
  });

  it("shows Added at once when a live agenda item has the same text", () => {
    const page = {
      ...withTopics,
      agenda: [{ id: 5, text: "Decide the pricing model", can_remove: true }],
    };
    render(<SeriesView initial={page} />);
    expect(screen.getAllByRole("button", { name: "Added" }).length).toBe(1);
    expect(
      screen.getAllByRole("button", { name: "Add to agenda" }).length,
    ).toBe(4);
  });

  it("cannot remove an Added line it may not remove (the author or an admin can)", () => {
    const page = {
      ...teammateTopics,
      agenda: [{ id: 5, text: "Decide the pricing model", can_remove: false }],
    };
    render(<SeriesView initial={page} />);
    const added = screen.getByRole("button", {
      name: "Added",
    }) as HTMLButtonElement;
    expect(added.disabled).toBe(true);
    expect(added.title).toContain("Only the person who added it");
  });

  it("says why when adding is refused and leaves the row as it was", async () => {
    const user = userEvent.setup();
    mocks.addAgenda.mockRejectedValue(
      new ApiError(
        409,
        "agenda_full",
        "The agenda has as many items as fit. Remove one first.",
      ),
    );
    render(<SeriesView initial={withTopics} />);
    await user.click(
      screen.getAllByRole("button", { name: "Add to agenda" })[0],
    );
    expect(
      await screen.findByText(
        "The agenda has as many items as fit. Remove one first.",
      ),
    ).toBeTruthy();
    expect(
      screen.getAllByRole("button", { name: "Add to agenda" }).length,
    ).toBe(5);
  });
});
