// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AskView } from "@/components/app/ask-view";
import { GateText } from "@/components/billing/gate-text";
import { ApiError } from "@/lib/api/client";
import { read, REPO } from "./helpers";
import { join } from "node:path";

// The two refusals that have a way forward: 403 `plan_inactive` and 403 `hours_cap_reached`, with the captured
// sentences (docs/fe-captures/feature-gates/03, docs/fe-captures/hours-usage/19).

const mocks = vi.hoisted(() => ({ ask: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { ask: { ask: mocks.ask } } }));

const captured = (path: string) => JSON.parse(read(join(REPO, path))).response.body.error as { code: string; message: string };
const planInactive = captured("docs/fe-captures/feature-gates/03-enforce-noplan-ask.json");
const noHours = captured("docs/fe-captures/hours-usage/19-start-meeting-no-hours.json");

afterEach(() => {
  vi.clearAllMocks();
  cleanup();
});

describe("GateText", () => {
  it("leads a plan_inactive sentence to the plans screen, keeping the sentence as it is", () => {
    render(<GateText message={planInactive.message} />);
    expect(screen.getByText(/Asking Beyric needs an active plan or trial\./)).toBeTruthy();
    expect(screen.getByRole("link", { name: "See plans" }).getAttribute("href")).toBe("/billing/plans");
  });

  it("leads an out-of-hours sentence to Billing", () => {
    render(<GateText message={noHours.message} />);
    expect(screen.getByRole("link", { name: "Add hours or choose a plan" }).getAttribute("href")).toBe("/billing");
  });

  it("adds nothing to any other sentence, so a wait (the reserve) is not sent to buy anything", () => {
    render(
      <GateText message="You do not have enough recorded hours left to record another meeting at the same time: hours are set aside for each meeting that is running. Try again once a meeting has finished." />,
    );
    expect(screen.queryByRole("link")).toBeNull();
  });
});

describe("Ask Beyric without a plan", () => {
  it("shows the refusal with the way to plans and offers no Try again (it is not transient)", async () => {
    mocks.ask.mockRejectedValue(new ApiError(403, planInactive.code, planInactive.message));
    render(<AskView scope={null} initials="AE" />);
    await userEvent.type(screen.getByRole("textbox"), "What did we decide?{enter}");
    await waitFor(() => expect(screen.getByRole("link", { name: "See plans" })).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    expect(screen.getByText(/needs an active plan or trial/)).toBeTruthy();
  });
});
