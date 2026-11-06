// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingView } from "@/components/app/meeting-view";
import type { Meeting } from "@/lib/api/types";
import { captured } from "./helpers";

// The meeting page of a recurring meeting: the Series row in Details and the question pills in the
// notes (docs/fe-captures/series-w8/15-meeting-detail.json for the two new fields).

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/browser", () => ({ api: { questions: { update: vi.fn() }, meetings: {}, tasks: {}, series: { list: vi.fn().mockResolvedValue({ timezone: "UTC", series: [] }), create: vi.fn() } } }));

const base = captured<Meeting>("47-meeting-detail-ready.json");
const withQuestions = {
  ...base,
  notes_html: "<h2>Summary</h2><p>Short.</p><h2>Open questions</h2><ul><li>Which pricing model will the team choose?</li></ul>",
  series: { id: 1, title: "Beyric Monday planning" },
  open_questions: [
    { id: 3, text: "Which pricing model will the team choose?", carriable: true, carry: true, resolved: false, can_update: true },
  ],
};

beforeEach(() => cleanup());

describe("the Series row", () => {
  it("links to the Series page from Details", () => {
    render(<MeetingView initial={withQuestions} templates={[]} />);
    const details = document.querySelector(".details") as HTMLElement;
    expect(within(details).getByText("Series")).toBeTruthy();
    expect(within(details).getByRole("link", { name: "Beyric Monday planning" }).getAttribute("href")).toBe("/series/1");
  });

  it("is left out for a teammate reading a meeting that is in no series", () => {
    render(<MeetingView initial={{ ...withQuestions, series: null, can_share: false }} templates={[]} />);
    expect((document.querySelector(".details") as HTMLElement).textContent).not.toContain("Series");
  });
});

describe("the question pills", () => {
  it("replace the plain list of a meeting in a series", () => {
    render(<MeetingView initial={withQuestions} templates={[]} />);
    expect(screen.getByRole("button", { name: "On next agenda" })).toBeTruthy();
  });

  it("are not drawn for a meeting in no series: the notes' own list stays", () => {
    render(<MeetingView initial={{ ...withQuestions, series: null, open_questions: [{ ...withQuestions.open_questions[0], carriable: false }] }} templates={[]} />);
    expect(screen.queryByRole("button", { name: /next agenda/ })).toBeNull();
    expect(screen.getByText("Which pricing model will the team choose?")).toBeTruthy();
  });
});
