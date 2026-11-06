// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { join } from "node:path";
import { SettingsPlannedSections } from "@/components/app/settings-planned-sections";
import { ApiError } from "@/lib/api/client";
import type { Team } from "@/lib/api/types";
import { REPO, read } from "./helpers";

// The live reminders row of "Sprints and reminders", fed the team body captured from the running
// app (docs/fe-captures/series-w8/50-team-before.json).

const mocks = vi.hoisted(() => ({ update: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { team: { update: mocks.update }, settings: { tasks: { set: vi.fn() } } } }));

const team = JSON.parse(read(join(REPO, "docs/fe-captures/series-w8/50-team-before.json"))).response.body as Team;

beforeEach(() => {
  cleanup();
  mocks.update.mockReset();
});

const card = () => screen.getByRole("heading", { name: "Sprints and reminders" }).closest("section")!;

describe("for an owner or admin", () => {
  it("starts off, as the server has it, with no timing select until it is on", () => {
    render(<SettingsPlannedSections team={team} />);
    const toggle = within(card()).getByRole("switch", { name: "Remind attendees before recurring meetings" });
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    expect((toggle as HTMLButtonElement).disabled).toBe(false);
    expect(within(card()).queryByRole("combobox", { name: "Reminder timing" })).toBeNull();
  });

  it("switches on and saves, then offers the designer's three timings", async () => {
    mocks.update.mockResolvedValue({ ...team, reminders_on: true });
    render(<SettingsPlannedSections team={team} />);
    await userEvent.click(within(card()).getByRole("switch", { name: "Remind attendees before recurring meetings" }));
    expect(mocks.update).toHaveBeenCalledWith({ reminders_on: true });
    const select = await within(card()).findByRole("combobox", { name: "Reminder timing" });
    expect([...(select as HTMLSelectElement).options].map((o) => o.text)).toEqual([
      "1 hour before",
      "30 minutes before",
      "1 hour and 30 minutes before",
    ]);
    expect((select as HTMLSelectElement).value).toBe("both");
  });

  it("saves a new timing", async () => {
    mocks.update.mockResolvedValue({ ...team, reminders_on: true, reminder_timing: "30m" });
    render(<SettingsPlannedSections team={{ ...team, reminders_on: true }} />);
    await userEvent.selectOptions(within(card()).getByRole("combobox", { name: "Reminder timing" }), "30m");
    expect(mocks.update).toHaveBeenCalledWith({ reminder_timing: "30m" });
  });

  it("leaves the switch where it was and says so when the save fails", async () => {
    mocks.update.mockRejectedValue(new ApiError(500, "internal_error", "boom"));
    render(<SettingsPlannedSections team={team} />);
    await userEvent.click(within(card()).getByRole("switch", { name: "Remind attendees before recurring meetings" }));
    expect(await within(card()).findByText("boom")).toBeTruthy();
    expect(within(card()).getByRole("switch", { name: "Remind attendees before recurring meetings" }).getAttribute("aria-checked")).toBe("false");
  });
});

describe("for everyone else", () => {
  it("shows the team's setting with the controls switched off", () => {
    render(<SettingsPlannedSections team={{ ...team, can_manage: false, reminders_on: true }} />);
    const toggle = within(card()).getByRole("switch", { name: "Remind attendees before recurring meetings" });
    expect((toggle as HTMLButtonElement).disabled).toBe(true);
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    expect((within(card()).getByRole("combobox", { name: "Reminder timing" }) as HTMLSelectElement).disabled).toBe(true);
  });
});

describe("the sprint rows", () => {
  it("stay off, with one 'not available yet' line that is about sprints", () => {
    render(<SettingsPlannedSections team={team} />);
    expect(within(card()).getAllByText(/not available yet/i)).toHaveLength(1);
    expect(within(card()).getByText("Sprint settings are not available yet.")).toBeTruthy();
    expect((within(card()).getByRole("combobox", { name: "Sprint length" }) as HTMLSelectElement).disabled).toBe(true);
  });
});
