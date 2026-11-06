// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OpenQuestions } from "@/components/app/open-questions";
import { NotesHtml } from "@/components/app/notes-html";
import type { OpenQuestion } from "@/lib/api/types";

// The meeting page's question pills ("Add to next agenda", "On next agenda", "Resolved"), shaped
// like docs/fe-captures/series-w8/15-meeting-detail.json.

const mocks = vi.hoisted(() => ({ update: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { questions: { update: mocks.update } } }));

const q = (over: Partial<OpenQuestion> = {}): OpenQuestion => ({
  id: 3,
  text: "Which pricing model will the team choose?",
  carriable: true,
  carry: true,
  resolved: false,
  can_update: true,
  ...over,
});

beforeEach(() => {
  cleanup();
  mocks.update.mockReset();
});

describe("the pill", () => {
  it.each([
    [q(), "On next agenda"],
    [q({ carry: false }), "Add to next agenda"],
    [q({ resolved: true }), "Resolved"],
  ])("reads as the designer's three states", (question, label) => {
    render(<OpenQuestions initial={[question]} heading="Open questions" />);
    expect(screen.getByRole("button", { name: label })).toBeTruthy();
  });

  it("draws no pill for a question of a meeting that is in no series", () => {
    render(<OpenQuestions initial={[q({ carriable: false })]} heading="Open questions" />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("Which pricing model will the team choose?")).toBeTruthy();
  });

  it("turns carrying off when it is on, and on when it is off", async () => {
    mocks.update.mockResolvedValueOnce(q({ carry: false })).mockResolvedValueOnce(q({ carry: true }));
    render(<OpenQuestions initial={[q()]} heading="Open questions" />);
    await userEvent.click(screen.getByRole("button", { name: "On next agenda" }));
    expect(mocks.update).toHaveBeenLastCalledWith(3, { carry: false });
    await userEvent.click(await screen.findByRole("button", { name: "Add to next agenda" }));
    expect(mocks.update).toHaveBeenLastCalledWith(3, { carry: true });
    expect(await screen.findByRole("button", { name: "On next agenda" })).toBeTruthy();
  });

  it("reopens a resolved question by putting it back on the agenda", async () => {
    mocks.update.mockResolvedValue(q());
    render(<OpenQuestions initial={[q({ resolved: true })]} heading="Open questions" />);
    await userEvent.click(screen.getByRole("button", { name: "Resolved" }));
    expect(mocks.update).toHaveBeenCalledWith(3, { carry: true });
  });

  it("is off for someone who cannot change it, and says so when a save fails", async () => {
    const { unmount } = render(<OpenQuestions initial={[q({ can_update: false })]} heading="Open questions" />);
    expect((screen.getByRole("button", { name: "On next agenda" }) as HTMLButtonElement).disabled).toBe(true);
    unmount();
    mocks.update.mockRejectedValue(new Error("down"));
    render(<OpenQuestions initial={[q()]} heading="Open questions" />);
    await userEvent.click(screen.getByRole("button", { name: "On next agenda" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: "On next agenda" })).toBeTruthy(); // where it was
  });
});

describe("in the notes", () => {
  it("takes the place of the Open questions part and leaves the others alone", () => {
    const html = "<h2>Summary</h2><p>Short.</p><h2>Open questions</h2><ul><li>Which pricing model will the team choose?</li></ul>";
    const { container } = render(
      <NotesHtml html={html} questions={<OpenQuestions initial={[q()]} heading="Open questions" />} />,
    );
    expect(container.querySelectorAll("h2")).toHaveLength(2);
    expect(container.querySelectorAll(".notes-sec-questions li")).toHaveLength(1);
    expect(container.querySelector(".notes-sec-questions button")?.textContent).toBe("On next agenda");
    expect(screen.getByText("Short.")).toBeTruthy();
  });
});
