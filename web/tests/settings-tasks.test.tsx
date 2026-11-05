// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsPlannedSections } from "@/components/app/settings-planned-sections";
import { ApiError } from "@/lib/api/client";
import type { TasksSettings } from "@/lib/api/types";
import { tasksBody } from "./helpers";

const mocks = vi.hoisted(() => ({ set: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { settings: { tasks: { set: mocks.set } } } }));

const stored = tasksBody<TasksSettings>("75-settings-tasks.json");
const card = () => screen.getByRole("heading", { name: "Tasks" }).closest("section") as HTMLElement;

beforeEach(() => {
  mocks.set.mockReset();
});
afterEach(cleanup);

describe("Settings > Tasks", () => {
  it("makes the two switches real and leaves the tool and Slack rows drawn off", () => {
    render(<SettingsPlannedSections tasks={stored} />);
    const create = within(card()).getByRole("switch", { name: "Create tasks from action items" });
    const email = within(card()).getByRole("switch", { name: "Email assignees" });
    expect([create.getAttribute("aria-checked"), email.getAttribute("aria-checked")]).toEqual(["true", "true"]);
    expect((create as HTMLButtonElement).disabled).toBe(false);
    expect((within(card()).getByRole("button", { name: "Connect" }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(card()).getByRole("button", { name: "Connect Slack" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(card()).getByText("Sync with your project tool and Post tasks to Slack are not available yet.")).toBeTruthy();
  });

  it("saves both values together the moment a switch is flipped", async () => {
    mocks.set.mockResolvedValue({ auto_create_tasks: true, email_assignees: false });
    render(<SettingsPlannedSections tasks={stored} />);
    await userEvent.click(within(card()).getByRole("switch", { name: "Email assignees" }));
    expect(mocks.set).toHaveBeenCalledWith({ auto_create_tasks: true, email_assignees: false });
    await waitFor(() =>
      expect(within(card()).getByRole("switch", { name: "Email assignees" }).getAttribute("aria-checked")).toBe("false"),
    );
  });

  it("leaves the switch where it was and says so when the save fails", async () => {
    mocks.set.mockImplementation(
      () =>
        new Promise((_, reject) =>
          setTimeout(() => reject(new ApiError(500, "server_error", "Something went wrong on our side.")), 0),
        ),
    );
    render(<SettingsPlannedSections tasks={stored} />);
    const flip = within(card()).getByRole("switch", { name: "Create tasks from action items" });
    await userEvent.click(flip);
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeNull());
    expect(flip.getAttribute("aria-checked")).toBe("true");
  });
});
