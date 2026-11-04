// @vitest-environment jsdom
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsPlannedSections } from "@/components/app/settings-planned-sections";

// The Settings sections the designer drew below Calendar (Sprints and reminders, Tasks) before their
// features exist (the third, Default notes language, is live: settings-language.test.tsx): drawn exactly, every control off, nothing stored,
// nothing called, and one plain line in each section saying so.

const fetchSpy = vi.fn();
let cookieWrites = 0;

beforeEach(() => {
  fetchSpy.mockReset();
  vi.stubGlobal("fetch", fetchSpy);
  cookieWrites = 0;
  vi.spyOn(document, "cookie", "set").mockImplementation(() => {
    cookieWrites += 1;
  });
  window.localStorage.clear();
  window.sessionStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const section = (name: string) => screen.getByRole("region", { name });
const everyControl = (root: HTMLElement) =>
  Array.from(root.querySelectorAll<HTMLElement>("button, select, input, textarea, a[href], [role=switch]"));

describe("the sections and their words, as designed", () => {
  it("draws the two sections in the designer's order", () => {
    const { container } = render(<SettingsPlannedSections />);
    const titles = Array.from(container.querySelectorAll("h2")).map((h) => h.textContent);
    expect(titles).toEqual(["Sprints and reminders", "Tasks"]);
  });

  it("Sprints and reminders: the pill, both toggles, the select and the designer's lines", () => {
    render(<SettingsPlannedSections />);
    const s = section("Sprints and reminders");
    expect(within(s).getByText("Workspace defaults. Projects can use their own sprint length.")).toBeTruthy();
    expect(within(s).getByText("Owners and admins")).toBeTruthy();
    expect(within(s).getByText("Sprint length")).toBeTruthy();
    expect(within(s).getByText("Open and close sprints at recurring meetings")).toBeTruthy();
    expect(within(s).getByText("Available for 1 week, 5 working day sprints.")).toBeTruthy();
    expect(within(s).getByText("Remind attendees before recurring meetings")).toBeTruthy();
    expect(
      within(s).getByText(
        "The email covers what the last meeting focused on, decisions made, open questions and unfinished tasks.",
      ),
    ).toBeTruthy();
    const length = within(s).getByLabelText("Sprint length") as HTMLSelectElement;
    expect(Array.from(length.options).map((o) => o.text)).toEqual([
      "1 week, 5 working days",
      "1 week, 7 days",
      "2 weeks",
      "1 month, 30 days",
      "3 months",
      "6 months",
      "Custom",
    ]);
    expect(length.value).toBe("week5");
    const timing = within(s).getByLabelText("Reminder timing") as HTMLSelectElement;
    expect(Array.from(timing.options).map((o) => o.text)).toEqual([
      "1 hour before",
      "30 minutes before",
      "1 hour and 30 minutes before",
    ]);
    expect(timing.value).toBe("1 hour and 30 minutes before");
  });

  it("Tasks: both toggles, the tool select with Connect, and Slack with Connect Slack", () => {
    render(<SettingsPlannedSections />);
    const s = section("Tasks");
    expect(within(s).getByText("What happens to action items after a meeting ends.")).toBeTruthy();
    expect(within(s).getByText("Create tasks from action items")).toBeTruthy();
    expect(
      within(s).getByText("Each action item becomes a task assigned to the teammate named as its owner."),
    ).toBeTruthy();
    expect(within(s).getByText("Email assignees")).toBeTruthy();
    expect(within(s).getByText("Send each person a list of their new tasks after the meeting.")).toBeTruthy();
    expect(within(s).getByText("Sync with your project tool")).toBeTruthy();
    expect(within(s).getByText("Send tasks, projects and sprints to a tool your team already uses.")).toBeTruthy();
    const tool = within(s).getByLabelText("Project tool") as HTMLSelectElement;
    expect(Array.from(tool.options).map((o) => o.text)).toEqual(["Jira", "Linear", "Asana", "Taiga"]);
    expect(within(s).getByRole("button", { name: "Connect" })).toBeTruthy();
    expect(within(s).getByText("Post tasks to Slack")).toBeTruthy();
    expect(within(s).getByText("Share new tasks in a channel so the team can track them there.")).toBeTruthy();
    expect(within(s).getByRole("button", { name: "Connect Slack" })).toBeTruthy();
  });
});

describe("nothing is on, nothing is connected, nothing is invented", () => {
  it("shows every switch off and no connected state or sample figure", () => {
    const { container } = render(<SettingsPlannedSections />);
    const switches = screen.getAllByRole("switch");
    expect(switches).toHaveLength(4);
    for (const toggle of switches) expect(toggle.getAttribute("aria-checked")).toBe("false");
    const text = container.textContent ?? "";
    for (const invented of [
      "Sprint 12",
      "Current sprint",
      "Syncing with",
      "#beyric-updates",
      "Beyric Monday planning",
      "Beyric Friday review",
      "days left",
      "Disconnect",
    ]) {
      expect(text).not.toContain(invented);
    }
  });

  it("carries exactly one 'not available yet' line in each section, and no other added words", () => {
    render(<SettingsPlannedSections />);
    expect(screen.getAllByText(/not available yet/i)).toHaveLength(2);
    for (const name of ["Sprints and reminders", "Tasks"]) {
      expect(within(section(name)).getAllByText(/not available yet/i)).toHaveLength(1);
    }
  });
});

describe("every control is off", () => {
  it("has no control a person could use: each is disabled, none is a link", () => {
    const { container } = render(<SettingsPlannedSections />);
    const controls = everyControl(container);
    expect(controls).toHaveLength(9); // 4 switches, 3 selects, the Connect and Connect Slack buttons
    for (const control of controls) {
      expect(control.tagName).not.toBe("A");
      const off = (control as HTMLButtonElement).disabled || control.getAttribute("aria-disabled") === "true";
      expect(off, control.outerHTML).toBe(true);
    }
  });

  it("uses real `disabled` on the buttons and the selects, and also marks the switches", () => {
    render(<SettingsPlannedSections />);
    for (const button of screen.getAllByRole("button")) expect((button as HTMLButtonElement).disabled).toBe(true);
    for (const select of screen.getAllByRole("combobox")) expect((select as HTMLSelectElement).disabled).toBe(true);
    for (const toggle of screen.getAllByRole("switch")) {
      expect((toggle as HTMLButtonElement).disabled).toBe(true);
      expect(toggle.getAttribute("aria-disabled")).toBe("true");
    }
  });

  it("every control has a name a screen reader can say", () => {
    const { container } = render(<SettingsPlannedSections />);
    for (const control of everyControl(container)) {
      const name =
        control.getAttribute("aria-label") ??
        control.textContent?.trim() ??
        container.querySelector(`label[for="${control.id}"]`)?.textContent;
      expect(name, control.outerHTML).toBeTruthy();
    }
  });
});

describe("pressing, typing or changing does nothing and stores nothing", () => {
  it("makes no request, writes no cookie or storage and changes no value", async () => {
    const user = userEvent.setup();
    const { container } = render(<SettingsPlannedSections />);
    const before = container.innerHTML;
    for (const control of everyControl(container)) {
      await user.click(control);
      if (control.tagName === "SELECT") {
        const select = control as HTMLSelectElement;
        const other = Array.from(select.options).find((o) => o.value !== select.value)!;
        fireEvent.change(select, { target: { value: other.value } });
        await user.selectOptions(select, other.value).catch(() => undefined);
        expect(select.value, select.id).toBe(select.options[select.selectedIndex].value);
      }
      control.focus();
      await user.keyboard("{Enter} ");
    }
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(cookieWrites).toBe(0);
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    for (const toggle of screen.getAllByRole("switch")) expect(toggle.getAttribute("aria-checked")).toBe("false");
    expect(container.innerHTML).toBe(before);
  });

  it("is not a form, so nothing can be submitted", () => {
    const { container } = render(<SettingsPlannedSections />);
    expect(container.querySelector("form")).toBeNull();
  });

  it("is plain server markup: no client code, no state, no network", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const source = readFileSync(join(__dirname, "../components/app/settings-planned-sections.tsx"), "utf8");
    expect(source).not.toMatch(/"use client"|useState|useEffect|fetch\(|localStorage|sessionStorage|api\./);
  });
});
