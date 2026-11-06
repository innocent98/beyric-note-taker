// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { join } from "node:path";
import { SeriesView } from "@/components/app/series-view";
import { ApiError } from "@/lib/api/client";
import type { SeriesPage } from "@/lib/api/types";
import { REPO, read } from "./helpers";

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
    series: { get: mocks.get, addAgenda: mocks.addAgenda, removeAgenda: mocks.removeAgenda, sendAgenda: mocks.sendAgenda },
    questions: { update: mocks.update },
    tasks: { list: mocks.tasksList },
  },
}));

const capture = (file: string) =>
  JSON.parse(read(join(REPO, "docs/fe-captures/series-w8", file))).response.body as SeriesPage;
const owner = capture("11-page-owner.json");
const teammate = capture("12-page-teammate.json");

beforeEach(() => {
  cleanup();
  Object.values(mocks).forEach((fn) => fn.mockReset());
  mocks.tasksList.mockResolvedValue({ people: [] });
});

describe("the page the designer drew", () => {
  it("has the header, the next meeting, the button and the five blocks in order", () => {
    render(<SeriesView initial={owner} />);
    expect(screen.getByRole("heading", { level: 1, name: "Beyric Monday planning" })).toBeTruthy();
    expect(screen.getByText("Weekly on Mondays, 14:00 · Google Meet")).toBeTruthy();
    expect(screen.getByText("Next meeting")).toBeTruthy();
    expect(screen.getByText("Mon 9 Nov, 14:00")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send agenda to attendees" })).toBeTruthy();
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
      "Unfinished from last time",
      "Finished since last meeting",
      "Open questions carried over",
      "Other agenda items",
    ]);
    expect(screen.getByText("Past meetings")).toBeTruthy();
    expect(screen.getByText("Attendees")).toBeTruthy();
    expect(screen.getByRole("link", { name: "All meetings" }).getAttribute("href")).toBe("/");
  });

  it("draws every unfinished task with its status and owner, and 'Needs owner' when it has none", () => {
    render(<SeriesView initial={owner} />);
    expect(screen.getAllByText("Finish payment checkout").length).toBeGreaterThan(0);
    expect(screen.getAllByText("To do").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Needs owner").length).toBeGreaterThan(0);
    expect(screen.getAllByTitle("Dan Okoro").length).toBeGreaterThan(0);
  });

  it("draws a finished task with its tick and 'Done', and the count", () => {
    render(<SeriesView initial={owner} />);
    expect(screen.getByText("1 done")).toBeTruthy();
    expect(screen.getByText("Send the pricing draft")).toBeTruthy();
    expect(screen.getByText(/^Done /)).toBeTruthy();
    expect(screen.getByText(/Tasks from this series completed since/)).toBeTruthy();
  });

  it("lists each carried question once with where it came from", () => {
    render(<SeriesView initial={owner} />);
    expect(screen.getAllByText("Which pricing model will the team choose?")).toHaveLength(1);
    expect(screen.getAllByText("From Beyric Monday planning").length).toBe(2);
    expect(screen.getAllByRole("button", { name: "Mark resolved" })).toHaveLength(2);
  });

  it("lists past meetings as links with their length and task count, and the attendees", () => {
    render(<SeriesView initial={owner} />);
    const links = screen.getAllByRole("link").filter((a) => a.getAttribute("href")?.startsWith("/meetings/"));
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
    expect(screen.getByText("Nothing finished yet. Completed tasks will show here.")).toBeTruthy();
    expect(screen.getByText("No open questions.")).toBeTruthy();
    expect(screen.getByText("No meetings recorded in this series yet.")).toBeTruthy();
  });

  it("leaves out the Next meeting block when no date is known, and draws no sprint line", () => {
    render(<SeriesView initial={{ ...owner, next_text: null, next_starts_at: null }} />);
    expect(screen.queryByText("Next meeting")).toBeNull();
    expect(screen.queryByText(/sprint/i)).toBeNull();
  });
});

describe("Mark resolved", () => {
  it("resolves the question and takes it off the list", async () => {
    mocks.update.mockResolvedValue({ id: 3, text: "x", carriable: true, carry: true, resolved: true, can_update: true });
    render(<SeriesView initial={owner} />);
    const first = owner.questions[0];
    await userEvent.click(screen.getAllByRole("button", { name: "Mark resolved" })[0]);
    expect(mocks.update).toHaveBeenCalledWith(first.id, { resolved: true });
    await waitFor(() => expect(screen.queryByText(first.text)).toBeNull());
  });

  it("is switched off for someone who may read but not change", () => {
    render(<SeriesView initial={{ ...owner, questions: owner.questions.map((q) => ({ ...q, can_update: false })) }} />);
    for (const button of screen.getAllByRole("button", { name: "Mark resolved" })) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("says so and keeps the question when the save fails", async () => {
    mocks.update.mockRejectedValue(new ApiError(403, "team_forbidden", "You can't do that here. Ask an owner or admin of your team."));
    render(<SeriesView initial={owner} />);
    await userEvent.click(screen.getAllByRole("button", { name: "Mark resolved" })[0]);
    expect(await screen.findByText(/You can't do that here/)).toBeTruthy();
    expect(screen.getByText(owner.questions[0].text)).toBeTruthy();
  });
});

describe("the agenda", () => {
  it("adds a line on Add and on Enter, and clears the box", async () => {
    mocks.addAgenda.mockResolvedValue({ id: 9, text: "Hiring update", can_remove: true });
    render(<SeriesView initial={owner} />);
    const box = screen.getByLabelText("New agenda item");
    await userEvent.type(box, "Hiring update{Enter}");
    expect(mocks.addAgenda).toHaveBeenCalledWith(owner.id, "Hiring update");
    expect(await screen.findByText("Hiring update")).toBeTruthy();
    expect((box as HTMLInputElement).value).toBe("");
  });

  it("keeps Add off while the box is empty", () => {
    render(<SeriesView initial={owner} />);
    expect((screen.getByRole("button", { name: "Add" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows the server's refusal for a web address", async () => {
    mocks.addAgenda.mockRejectedValue(new ApiError(422, "invalid_agenda_item", "Write the item without a web address."));
    render(<SeriesView initial={owner} />);
    await userEvent.type(screen.getByLabelText("New agenda item"), "see https://x.test{Enter}");
    expect(await screen.findByText("Write the item without a web address.")).toBeTruthy();
  });

  it("removes a line the person may remove, and draws no remove button for the others", async () => {
    mocks.removeAgenda.mockResolvedValue(undefined);
    const initial = { ...owner, agenda: [{ id: 1, text: "Mine", can_remove: true }, { id: 2, text: "Theirs", can_remove: false }] };
    render(<SeriesView initial={initial} />);
    expect(screen.queryByRole("button", { name: 'Remove "Theirs"' })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: 'Remove "Mine"' }));
    expect(mocks.removeAgenda).toHaveBeenCalledWith(owner.id, 1);
    await waitFor(() => expect(screen.queryByText("Mine")).toBeNull());
  });
});

describe("Send agenda to attendees", () => {
  it("sends, then flashes the number of attendees", async () => {
    mocks.sendAgenda.mockResolvedValue({ count: 1, names: ["Dan Okoro"] });
    render(<SeriesView initial={owner} />);
    await userEvent.click(screen.getByRole("button", { name: "Send agenda to attendees" }));
    expect(mocks.sendAgenda).toHaveBeenCalledWith(owner.id);
    expect(await screen.findByText("Agenda sent to 1 attendee")).toBeTruthy();
  });

  it("is switched off, with the reason, when nobody would receive it", () => {
    render(<SeriesView initial={{ ...owner, agenda_recipients: 0 }} />);
    const button = screen.getByRole("button", { name: "Send agenda to attendees" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.title).toMatch(/nobody to send it to/);
  });

  it.each([
    ["no_attendees", 409, "There is nobody on your team to send it to yet."],
    ["agenda_rate_limited", 429, "You have sent as many agendas as fit in a day. Try again tomorrow."],
  ])("shows the server's words for %s", async (code, status, message) => {
    mocks.sendAgenda.mockRejectedValue(new ApiError(status, code, message));
    render(<SeriesView initial={owner} />);
    await userEvent.click(screen.getByRole("button", { name: "Send agenda to attendees" }));
    expect(await screen.findByText(message)).toBeTruthy();
  });
});

describe("opening a task", () => {
  it("opens the same drawer as the board", async () => {
    render(<SeriesView initial={owner} />);
    const row = screen.getAllByText("Finish payment checkout")[0].closest("[role=button]") as HTMLElement;
    await userEvent.click(row);
    await waitFor(() => expect(mocks.tasksList).toHaveBeenCalled());
    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(within(screen.getByRole("dialog")).getAllByText("Finish payment checkout").length).toBeGreaterThan(0);
  });
});
