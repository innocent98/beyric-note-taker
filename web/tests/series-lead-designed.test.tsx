// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingView } from "@/components/app/meeting-view";
import { RecurringHint } from "@/components/app/recurring-hint";
import { SettingsPlannedSections } from "@/components/app/settings-planned-sections";
import { ApiError } from "@/lib/api/client";
import type { Meeting, Team } from "@/lib/api/types";
import { captured } from "./helpers";

// The three pieces the designer did not draw and Adebayo approved lead-designing (2026-11-06):
// "Add to recurring meeting", the "Make this a recurring meeting?" chip and the person's own
// agenda and reminder mail switch.

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(),
  setSeries: vi.fn(),
  makeRecurring: vi.fn(),
  get: vi.fn(),
  setReminders: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    series: { list: mocks.list, create: mocks.create },
    meetings: { setSeries: mocks.setSeries, makeRecurring: mocks.makeRecurring, get: mocks.get },
    questions: { update: vi.fn() },
    tasks: {},
    milestones: { forMeeting: () => Promise.resolve({ milestones: [] }), confirm: vi.fn() },
    team: { update: vi.fn() },
    settings: { reminders: { set: mocks.setReminders }, tasks: { set: vi.fn() } },
  },
}));

const base = captured<Meeting>("47-meeting-detail-ready.json");
const lone: Meeting = { ...base, series: null, suggest_recurring: false, open_questions: [], can_share: true };
const team = { id: 2, name: "My team", share_new_meetings: false, reminders_on: false, reminder_timing: "both", my_role: "owner", member_count: 2, pending_invites: 0, can_manage: true, can_change_roles: true } as Team;

beforeEach(() => {
  cleanup();
  Object.values(mocks).forEach((fn) => fn.mockReset());
  mocks.list.mockResolvedValue({ timezone: "UTC", series: [{ id: 1, title: "Beyric Monday planning" }] });
  mocks.get.mockResolvedValue(lone);
  window.localStorage.clear();
  HTMLDialogElement.prototype.close = vi.fn();
});

const openDialog = async () => {
  const dialog = document.getElementById("series-dialog") as HTMLDialogElement;
  dialog.setAttribute("open", ""); // what showModal does to the attribute the picker watches
  await waitFor(() => expect(mocks.list).toHaveBeenCalled());
  return dialog;
};

describe("Add to recurring meeting (lead-designed)", () => {
  it("is the Series row's entry for the owner of a ready meeting in no series", () => {
    render(<MeetingView initial={lone} templates={[]} />);
    const details = document.querySelector(".details") as HTMLElement;
    const entry = within(details).getByRole("button", { name: "Add to recurring meeting" });
    expect(entry.getAttribute("data-dialog-open")).toBe("series-dialog");
  });

  it("offers Change beside the link when the meeting is already in one, and nothing to a teammate", () => {
    const inOne = { ...lone, series: { id: 1, title: "Beyric Monday planning" } };
    const { unmount } = render(<MeetingView initial={inOne} templates={[]} />);
    expect(screen.getByRole("button", { name: "Change" })).toBeTruthy();
    unmount();
    render(<MeetingView initial={{ ...inOne, can_share: false }} templates={[]} />);
    expect(screen.queryByRole("button", { name: "Change" })).toBeNull();
    expect(screen.getByRole("link", { name: "Beyric Monday planning" })).toBeTruthy();
  });

  it("puts the meeting into the series that was chosen", async () => {
    mocks.setSeries.mockResolvedValue({ series: { id: 1, title: "Beyric Monday planning" } });
    render(<MeetingView initial={lone} templates={[]} />);
    const dialog = await openDialog();
    await userEvent.selectOptions(within(dialog).getByLabelText("Recurring meeting"), "1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(mocks.setSeries).toHaveBeenCalledWith(lone.id, 1));
    await waitFor(() => expect(mocks.get).toHaveBeenCalled()); // the page reads the meeting again
  });

  it("makes a new recurring meeting from a name, then puts the meeting in it", async () => {
    mocks.create.mockResolvedValue({ id: 7, title: "Design sync" });
    mocks.setSeries.mockResolvedValue({ series: { id: 7, title: "Design sync" } });
    render(<MeetingView initial={lone} templates={[]} />);
    const dialog = await openDialog();
    const add = within(dialog).getByRole("button", { name: "Add" }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    await userEvent.selectOptions(within(dialog).getByLabelText("Recurring meeting"), "new");
    expect(add.disabled).toBe(true); // a name is needed
    await userEvent.type(within(dialog).getByLabelText("Name"), "Design sync");
    await userEvent.click(add);
    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith("Design sync"));
    expect(mocks.setSeries).toHaveBeenCalledWith(lone.id, 7);
  });

  it("takes a meeting out of its series", async () => {
    mocks.setSeries.mockResolvedValue({ series: null });
    render(<MeetingView initial={{ ...lone, series: { id: 1, title: "Beyric Monday planning" } }} templates={[]} />);
    const dialog = await openDialog();
    await userEvent.selectOptions(within(dialog).getByLabelText("Recurring meeting"), "none");
    await userEvent.click(within(dialog).getByRole("button", { name: "Take out" }));
    await waitFor(() => expect(mocks.setSeries).toHaveBeenCalledWith(lone.id, null));
  });

  it("says so and stays open when the save fails", async () => {
    mocks.setSeries.mockRejectedValue(new ApiError(500, "internal_error", "boom"));
    render(<MeetingView initial={lone} templates={[]} />);
    const dialog = await openDialog();
    await userEvent.selectOptions(within(dialog).getByLabelText("Recurring meeting"), "1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add" }));
    expect(await within(dialog).findByText("boom")).toBeTruthy();
    expect(HTMLDialogElement.prototype.close).not.toHaveBeenCalled();
  });
});

describe("Make this a recurring meeting? (lead-designed)", () => {
  it("is drawn on a meeting that suggests it, and not on one that does not", async () => {
    const { unmount } = render(<MeetingView initial={{ ...lone, suggest_recurring: true }} templates={[]} />);
    expect(await screen.findByText("Make this a recurring meeting?")).toBeTruthy();
    unmount();
    render(<MeetingView initial={lone} templates={[]} />);
    expect(screen.queryByText("Make this a recurring meeting?")).toBeNull();
  });

  it("makes the series from the link and reads the meeting again", async () => {
    mocks.makeRecurring.mockResolvedValue({ series: { id: 3, title: "x" } });
    const done = vi.fn();
    render(<RecurringHint meetingId={9} onDone={done} />);
    await userEvent.click(await screen.findByRole("button", { name: "Make recurring" }));
    expect(mocks.makeRecurring).toHaveBeenCalledWith(9);
    await waitFor(() => expect(done).toHaveBeenCalled());
  });

  it("is hidden by Not now, and stays hidden after a reload", async () => {
    const { unmount } = render(<RecurringHint meetingId={11} onDone={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Not now" }));
    expect(screen.queryByText("Make this a recurring meeting?")).toBeNull();
    unmount();
    render(<RecurringHint meetingId={11} onDone={vi.fn()} />);
    expect(screen.queryByText("Make this a recurring meeting?")).toBeNull();
  });

  it("shows the server's words when it cannot be done", async () => {
    mocks.makeRecurring.mockRejectedValue(new ApiError(409, "not_reused", "This link has not been used for another meeting, so it is not recurring."));
    render(<RecurringHint meetingId={12} onDone={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Make recurring" }));
    expect(await screen.findByText(/has not been used for another meeting/)).toBeTruthy();
  });
});

describe("the person's own reminder mail switch (lead-designed)", () => {
  const card = () => screen.getByRole("heading", { name: "Sprints and reminders" }).closest("section")!;

  it("sits under the team's reminders row, live for everyone, as the person has it", () => {
    render(<SettingsPlannedSections team={{ ...team, can_manage: false }} reminderMail={{ notify_reminders: true }} />);
    const toggle = within(card()).getByRole("switch", { name: "Email me agendas and reminders" }) as HTMLButtonElement;
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    expect(toggle.disabled).toBe(false); // not only owners and admins
  });

  it("saves the flip, and leaves it where it was when the save fails", async () => {
    mocks.setReminders.mockResolvedValueOnce({ notify_reminders: false }).mockRejectedValueOnce(new ApiError(500, "internal_error", "boom"));
    render(<SettingsPlannedSections team={team} reminderMail={{ notify_reminders: true }} />);
    const toggle = () => within(card()).getByRole("switch", { name: "Email me agendas and reminders" });
    await userEvent.click(toggle());
    expect(mocks.setReminders).toHaveBeenLastCalledWith(false);
    await waitFor(() => expect(toggle().getAttribute("aria-checked")).toBe("false"));
    await userEvent.click(toggle());
    expect(await within(card()).findByText("boom")).toBeTruthy();
    expect(toggle().getAttribute("aria-checked")).toBe("false");
  });

  it("is not drawn when the preference could not be read", () => {
    render(<SettingsPlannedSections team={team} />);
    expect(within(card()).queryByRole("switch", { name: "Email me agendas and reminders" })).toBeNull();
  });
});
