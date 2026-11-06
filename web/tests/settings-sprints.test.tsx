// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsPlannedSections } from "@/components/app/settings-planned-sections";
import { ApiError } from "@/lib/api/client";
import type { SprintList, Team } from "@/lib/api/types";
import { sprintsBody } from "./helpers";

// Settings > Sprints and reminders, live (W9), fed the bodies captured from the running API
// (docs/fe-captures/sprints-w9/): 10 the owner's team (week5, aligned), 09 its sprints, 44 the admin's
// answer after choosing Custom, 45 the sprints after that, 43 the refusal of the Monday to Friday
// switch on any other length.

const mocks = vi.hoisted(() => ({ update: vi.fn(), list: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { team: { update: mocks.update }, sprints: { list: mocks.list } } }));

const team = sprintsBody<Team>("10-team.json");
const sprints = sprintsBody<SprintList>("09-sprints-first.json");
const custom = sprintsBody<Team>("44-admin-sets-custom.json");
const afterCustom = sprintsBody<SprintList>("45-sprints-after-settings.json");
const card = () => screen.getByRole("heading", { name: "Sprints and reminders" }).closest("section") as HTMLElement;
const draw = (t: Team = team, list: SprintList | null = sprints) => render(<SettingsPlannedSections team={t} sprints={list} />);

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-04T12:00:00Z"));
  Object.values(mocks).forEach((fn) => fn.mockReset());
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("Settings > Sprints and reminders", () => {
  it("shows the length, the current sprint under its title and the Monday to Friday switch on", () => {
    draw();
    const select = within(card()).getByRole("combobox", { name: "Sprint length" }) as HTMLSelectElement;
    expect(select.value).toBe("week5");
    expect(Array.from(select.options).map((o) => o.text)).toEqual([
      "1 week, 5 working days",
      "1 week, 7 days",
      "2 weeks",
      "1 month, 30 days",
      "3 months",
      "6 months",
      "Custom",
    ]);
    expect(within(card()).getByText("Current sprint: Sprint 1, Mon 2 Nov to Fri 6 Nov")).toBeTruthy();
    const align = within(card()).getByRole("switch", { name: "Open and close sprints at recurring meetings" });
    expect(align.getAttribute("aria-checked")).toBe("true");
    expect(within(card()).getByText("Sprints open at Monday planning and close at Friday review.")).toBeTruthy();
    expect(within(card()).queryByLabelText("Days per sprint")).toBeNull();
  });

  it("has no 'not available yet' line when the team could be read", () => {
    draw();
    expect(within(card()).queryAllByText(/not available yet/i)).toHaveLength(0);
  });

  it("saves a new length with one PATCH, shows the days box for Custom, and reads the preview again", async () => {
    mocks.update.mockResolvedValue(custom);
    mocks.list.mockResolvedValue(afterCustom);
    draw();
    await userEvent.selectOptions(within(card()).getByRole("combobox", { name: "Sprint length" }), "Custom");
    expect(mocks.update).toHaveBeenCalledWith({ sprint_length: "custom" });
    const days = (await within(card()).findByLabelText("Days per sprint")) as HTMLInputElement;
    expect(days.value).toBe(String(custom.sprint_custom_days));
    const current = afterCustom.sprints.find((s) => s.id === afterCustom.current_id)!;
    expect(mocks.list).toHaveBeenCalled();
    await waitFor(() => expect(within(card()).getByText(/^Current sprint: /).textContent).toContain(current.name));
    // Off Monday to Friday, the switch is off and says where it works.
    expect(within(card()).getByRole("switch", { name: "Open and close sprints at recurring meetings" }).getAttribute("aria-checked")).toBe("false");
    expect(within(card()).getByText("Available for 1 week, 5 working day sprints.")).toBeTruthy();
  });

  it("saves the number of days, clamped to 1 to 365, when the box is left", async () => {
    mocks.update.mockResolvedValue({ ...custom, sprint_custom_days: 365 });
    mocks.list.mockResolvedValue(afterCustom);
    draw(custom);
    const days = within(card()).getByLabelText("Days per sprint") as HTMLInputElement;
    await userEvent.clear(days);
    await userEvent.type(days, "900");
    await userEvent.tab();
    expect(mocks.update).toHaveBeenCalledWith({ sprint_custom_days: 365 });
    await waitFor(() => expect(days.value).toBe("365"));
  });

  it("does not save the same number of days again", async () => {
    draw(custom);
    const days = within(card()).getByLabelText("Days per sprint") as HTMLInputElement;
    await userEvent.click(days);
    await userEvent.tab();
    expect(mocks.update).not.toHaveBeenCalled();
  });

  it("flips the Monday to Friday switch with one PATCH on 1 week, 5 working days", async () => {
    mocks.update.mockResolvedValue({ ...team, sprint_align_weekdays: false });
    mocks.list.mockResolvedValue({ ...sprints, sprint_align_weekdays: false });
    draw();
    await userEvent.click(within(card()).getByRole("switch", { name: "Open and close sprints at recurring meetings" }));
    expect(mocks.update).toHaveBeenCalledWith({ sprint_align_weekdays: false });
    await waitFor(() =>
      expect(within(card()).getByRole("switch", { name: "Open and close sprints at recurring meetings" }).getAttribute("aria-checked")).toBe("false"),
    );
  });

  it("answers the switch on any other length with the designer's toast, and sends nothing", async () => {
    draw({ ...team, sprint_length: "week14" });
    await userEvent.click(within(card()).getByRole("switch", { name: "Open and close sprints at recurring meetings" }));
    expect(screen.getByText("Only available for 1 week, 5 working day sprints")).toBeTruthy();
    expect(mocks.update).not.toHaveBeenCalled();
  });

  it("puts the control back and shows the message when a change is refused", async () => {
    mocks.update.mockRejectedValue(new ApiError(403, "team_forbidden", "Only an owner or admin of the team can do that."));
    draw();
    await userEvent.selectOptions(within(card()).getByRole("combobox", { name: "Sprint length" }), "2 weeks");
    expect(await within(card()).findByText("Only an owner or admin of the team can do that.")).toBeTruthy();
    expect((within(card()).getByRole("combobox", { name: "Sprint length" }) as HTMLSelectElement).value).toBe("week5");
  });

  it("switches both rows off for a member and says who may change them", () => {
    draw({ ...team, my_role: "member", can_manage: false });
    expect((within(card()).getByRole("combobox", { name: "Sprint length" }) as HTMLSelectElement).disabled).toBe(true);
    expect((within(card()).getByRole("switch", { name: "Open and close sprints at recurring meetings" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(card()).getByText("Only an owner or admin of your team can change the sprint settings.")).toBeTruthy();
  });

  it("leaves the preview line out when the sprints could not be read, and draws everything off when the team could not", () => {
    const { unmount } = draw(team, null);
    expect(within(card()).queryByText(/^Current sprint:/)).toBeNull();
    expect((within(card()).getByRole("combobox", { name: "Sprint length" }) as HTMLSelectElement).disabled).toBe(false);
    unmount();
    render(<SettingsPlannedSections />);
    expect((within(card()).getByRole("combobox", { name: "Sprint length" }) as HTMLSelectElement).disabled).toBe(true);
    expect(within(card()).getByText("These settings are not available yet.")).toBeTruthy();
  });
});
