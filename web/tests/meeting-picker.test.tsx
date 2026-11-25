// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingPicker, type MeetingSelection } from "@/components/app/meeting-picker";
import { ApiError } from "@/lib/api/client";
import type { PickerPreview } from "@/lib/api/types";

// The meeting picker of the cross-meeting recipes. The preview bodies have the shape captured from
// the running app (docs/fe-captures/recipes-cross-meeting/); the meetings in them are placeholders.

const api = vi.hoisted(() => ({
  series: { list: vi.fn() },
  projects: { list: vi.fn() },
  sprints: { list: vi.fn() },
  meetings: { list: vi.fn() },
  recipes: { resolvePicker: vi.fn() },
}));
vi.mock("@/lib/api/browser", () => ({ api }));

function preview(over: Partial<PickerPreview> = {}): PickerPreview {
  return {
    meetings: [
      { id: 12, title: "Review", day: "2026-11-09" },
      { id: 11, title: "Kick-off", day: "2026-11-02" },
    ],
    left_out_count: 0,
    left_out_message: null,
    capped: false,
    max_meetings: 12,
    ...over,
  };
}

function setup(team?: number | null) {
  const onChange = vi.fn<(s: MeetingSelection) => void>();
  const user = userEvent.setup();
  render(<MeetingPicker id="mp" team={team} onChange={onChange} />);
  return { onChange, user };
}

beforeEach(() => {
  cleanup();
  vi.resetAllMocks();
  api.series.list.mockResolvedValue({ timezone: "UTC", series: [{ id: 3, title: "Monday planning" }] });
  api.projects.list.mockResolvedValue({ projects: [{ id: 5, name: "Onboarding" }] });
  api.sprints.list.mockResolvedValue({
    sprints: [
      { id: 8, name: "Sprint 1", state: "closed" },
      { id: 9, name: "Sprint 2", state: "current" },
    ],
  });
  api.meetings.list.mockResolvedValue({
    meetings: [
      { id: 21, title: "Alpha" },
      { id: 22, title: "Beta" },
    ],
  });
  api.recipes.resolvePicker.mockResolvedValue(preview());
});

describe("choosing a recurring meeting", () => {
  it("shows the meetings that choice means, with title and day, and reports them", async () => {
    const { user, onChange } = setup(7);
    await screen.findByRole("option", { name: "Monday planning" });
    await user.selectOptions(screen.getByLabelText("Recurring meeting"), "3");
    expect(await screen.findByText("Review")).toBeTruthy();
    expect(screen.getByText("9 Nov 2026")).toBeTruthy();
    expect(screen.getByText("2 meetings will be used")).toBeTruthy();
    expect(api.recipes.resolvePicker).toHaveBeenCalledWith({ series_id: 3 }, 7);
    expect(onChange).toHaveBeenLastCalledWith({ picker: { series_id: 3 }, meetingIds: [12, 11], leftOut: 0 });
  });

  it("asks for nothing and reports nothing until a recurring meeting is chosen", async () => {
    const { onChange } = setup();
    await screen.findByRole("option", { name: "Monday planning" });
    expect(api.recipes.resolvePicker).not.toHaveBeenCalled();
    expect(screen.queryByText(/will be used/)).toBeNull();
    expect(onChange).toHaveBeenLastCalledWith({ picker: null, meetingIds: [], leftOut: 0 });
  });
});

describe("the preview", () => {
  async function chooseSeries(user: ReturnType<typeof userEvent.setup>) {
    await screen.findByRole("option", { name: "Monday planning" });
    await user.selectOptions(screen.getByLabelText("Recurring meeting"), "3");
  }

  it("says how many meetings were left out, in the server's words, and names none", async () => {
    api.recipes.resolvePicker.mockResolvedValue(
      preview({
        meetings: [{ id: 12, title: "Review", day: "2026-11-09" }],
        left_out_count: 2,
        left_out_message: "2 meetings were left out because you cannot read them or they are not ready.",
      }),
    );
    const { user, onChange } = setup();
    await chooseSeries(user);
    expect(await screen.findByText(/2 meetings were left out because you cannot read them/)).toBeTruthy();
    expect(screen.getByText("1 meeting will be used")).toBeTruthy();
    expect(onChange).toHaveBeenLastCalledWith({ picker: { series_id: 3 }, meetingIds: [12], leftOut: 2 });
  });

  it("says when the cap cut some", async () => {
    api.recipes.resolvePicker.mockResolvedValue(preview({ capped: true }));
    const { user } = setup();
    await chooseSeries(user);
    expect(await screen.findByText("More than 12 meetings match. The 12 newest are used.")).toBeTruthy();
  });

  it("says plainly when nothing matches", async () => {
    api.recipes.resolvePicker.mockResolvedValue(preview({ meetings: [] }));
    const { user, onChange } = setup();
    await chooseSeries(user);
    expect(await screen.findByText("No meetings with notes match that choice.")).toBeTruthy();
    expect(onChange).toHaveBeenLastCalledWith({ picker: { series_id: 3 }, meetingIds: [], leftOut: 0 });
  });

  it("says the choice is gone when it is a 404, and reports nothing", async () => {
    api.recipes.resolvePicker.mockRejectedValue(new ApiError(404, "not_found", "No such recurring meeting."));
    const { user, onChange } = setup();
    await chooseSeries(user);
    expect(await screen.findByText("That choice is no longer available. Choose again.")).toBeTruthy();
    expect(onChange).toHaveBeenLastCalledWith({ picker: null, meetingIds: [], leftOut: 0 });
  });

  it("drops the answer to an older choice", async () => {
    let first: (p: PickerPreview) => void = () => {};
    api.recipes.resolvePicker
      .mockImplementationOnce(() => new Promise<PickerPreview>((resolve) => (first = resolve)))
      .mockResolvedValueOnce(preview({ meetings: [{ id: 30, title: "Newer choice", day: "2026-11-20" }] }));
    api.series.list.mockResolvedValue({
      timezone: "UTC",
      series: [
        { id: 3, title: "Monday planning" },
        { id: 4, title: "Friday review" },
      ],
    });
    const { user } = setup();
    await screen.findByRole("option", { name: "Friday review" });
    await user.selectOptions(screen.getByLabelText("Recurring meeting"), "3");
    await user.selectOptions(screen.getByLabelText("Recurring meeting"), "4");
    expect(await screen.findByText("Newer choice")).toBeTruthy();
    first(preview());
    await waitFor(() => expect(screen.queryByText("Kick-off")).toBeNull());
    expect(screen.getByText("Newer choice")).toBeTruthy();
  });
});

describe("the other ways", () => {
  it("a project", async () => {
    const { user } = setup(7);
    await user.selectOptions(screen.getByLabelText("Choose meetings by"), "project");
    await screen.findByRole("option", { name: "Onboarding" });
    await user.selectOptions(screen.getByLabelText("Project"), "5");
    await screen.findByText("Review");
    expect(api.projects.list).toHaveBeenCalledWith(7);
    expect(api.recipes.resolvePicker).toHaveBeenCalledWith({ project_id: 5 }, 7);
  });

  it("a sprint, naming the current one", async () => {
    const { user } = setup();
    await user.selectOptions(screen.getByLabelText("Choose meetings by"), "sprint");
    await screen.findByRole("option", { name: "Sprint 2 (current)" });
    expect(screen.getByRole("option", { name: "Sprint 1" })).toBeTruthy();
    await user.selectOptions(screen.getByLabelText("Sprint"), "9");
    await screen.findByText("Review");
    expect(api.recipes.resolvePicker).toHaveBeenCalledWith({ sprint_id: 9 }, undefined);
  });

  it("a date range, sent with the browser's zone", async () => {
    const { user } = setup();
    await user.selectOptions(screen.getByLabelText("Choose meetings by"), "dates");
    await user.type(screen.getByLabelText("From"), "2026-11-01");
    await user.type(screen.getByLabelText("To"), "2026-11-10");
    await screen.findByText("Review");
    expect(api.recipes.resolvePicker).toHaveBeenLastCalledWith(
      { from_day: "2026-11-01", to_day: "2026-11-10", tz: "UTC" },
      undefined,
    );
  });

  it("a date range that ends before it starts is explained and asks for nothing", async () => {
    const { user } = setup();
    await user.selectOptions(screen.getByLabelText("Choose meetings by"), "dates");
    await user.type(screen.getByLabelText("From"), "2026-11-10");
    await user.type(screen.getByLabelText("To"), "2026-11-01");
    expect(await screen.findByText("From must be on or before To.")).toBeTruthy();
    expect(api.recipes.resolvePicker).not.toHaveBeenCalledWith(expect.objectContaining({ to_day: "2026-11-01" }), undefined);
  });

  it("hand-picked meetings, at most twelve", async () => {
    api.meetings.list.mockResolvedValue({
      meetings: Array.from({ length: 14 }, (_, i) => ({ id: 100 + i, title: `Meeting ${i}` })),
    });
    const { user } = setup();
    await user.selectOptions(screen.getByLabelText("Choose meetings by"), "pick");
    await screen.findByLabelText("Meeting 0");
    await user.click(screen.getByLabelText("Meeting 1"));
    await user.click(screen.getByLabelText("Meeting 0"));
    await waitFor(() => expect(api.recipes.resolvePicker).toHaveBeenLastCalledWith({ meeting_ids: [101, 100] }, undefined));
    for (let i = 2; i < 12; i++) await user.click(screen.getByLabelText(`Meeting ${i}`));
    expect((screen.getByLabelText("Meeting 12") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText("Meeting 3") as HTMLInputElement).disabled).toBe(false);
    await user.click(screen.getByLabelText("Meeting 0"));
    expect((screen.getByLabelText("Meeting 12") as HTMLInputElement).disabled).toBe(false);
  });
});
