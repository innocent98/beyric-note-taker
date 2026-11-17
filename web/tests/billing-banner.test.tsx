// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BillingBanner } from "@/components/billing/billing-banner";
import { ApiError } from "@/lib/api/client";
import { cap } from "./billing-fixtures";

// The app-wide notice for the grace and the lapse, on the captured states (50 grace, 57 grace with a 3-D Secure
// charge waiting, 61 lapsed, 26 active).

const mocks = vi.hoisted(() => ({ state: vi.fn(), payLink: vi.fn(), goTo: vi.fn(), path: "/tasks" }));
vi.mock("@/lib/api/browser", () => ({ api: { billing: { state: mocks.state, payLink: mocks.payLink } } }));
vi.mock("@/lib/navigate", () => ({ goTo: mocks.goTo }));
vi.mock("next/navigation", () => ({ usePathname: () => mocks.path }));

beforeEach(() => {
  window.sessionStorage.clear();
  mocks.path = "/tasks";
});
afterEach(() => {
  vi.clearAllMocks();
  cleanup();
});

describe("a plan in its grace", () => {
  it("says the payment failed, the date it ends, that no new hours come, and offers Pay now", async () => {
    mocks.state.mockResolvedValue(cap("50-subscription-past-due.json"));
    mocks.payLink.mockResolvedValue(cap("52-pay-link.json"));
    render(<BillingBanner />);
    expect(await screen.findByText(/Your plan payment did not go through/)).toBeTruthy();
    expect(screen.getByText(/No new hours are added until it is paid/)).toBeTruthy();
    expect(screen.getByText(/Fri 20 Nov 2026/)).toBeTruthy();
    expect(screen.getByText(/You can still record from the hours you have left/)).toBeTruthy();
    screen.getByRole("button", { name: "Pay now" }).click();
    await waitFor(() => expect(mocks.goTo).toHaveBeenCalledWith("https://fake.test/pay/bn-5-3d7b934f67285800"));
    expect(mocks.payLink).toHaveBeenCalledTimes(1);
  });

  it("sends a charge waiting for the bank to THAT charge's own page and never asks for a second checkout", async () => {
    mocks.state.mockResolvedValue(cap("57-subscription-3ds.json"));
    render(<BillingBanner />);
    const link = await screen.findByRole("link", { name: "Confirm with your bank" });
    expect(link.getAttribute("href")).toBe("https://fake.test/3ds/open-charge");
    expect(screen.queryByRole("button", { name: "Pay now" })).toBeNull();
    expect(mocks.payLink).not.toHaveBeenCalled();
  });
});

describe("a lapsed plan", () => {
  it("says what is closed and what stays, and offers a plan", async () => {
    mocks.state.mockResolvedValue({ ...cap<Record<string, unknown>>("61-subscription-lapsed-after-pass.json"), pending_payment: null });
    render(<BillingBanner />);
    expect(await screen.findByText(/Your plan has ended because a payment was not made/)).toBeTruthy();
    expect(screen.getByText(/Ask Beyric, translating and rewriting notes are closed/)).toBeTruthy();
    expect(screen.getByText(/except recordings from extra hours you still hold/)).toBeTruthy();
    expect(screen.getByText(/notes, transcripts and exports are all still here/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Choose a plan" }).getAttribute("href")).toBe("/billing/plans");
  });

  it("still offers the bank's page for a charge that waits, because paying it late brings the plan back", async () => {
    mocks.state.mockResolvedValue(cap("61-subscription-lapsed-after-pass.json"));
    render(<BillingBanner />);
    expect((await screen.findByRole("link", { name: "Confirm with your bank" })).getAttribute("href")).toBe("https://fake.test/3ds/open-charge");
  });
});

describe("when there is nothing to say", () => {
  it("draws nothing for an active plan", async () => {
    mocks.state.mockResolvedValue(cap("26-subscription-active.json"));
    const { container } = render(<BillingBanner />);
    await waitFor(() => expect(mocks.state).toHaveBeenCalled());
    expect(container.querySelector("[data-billing-banner]")).toBeNull();
  });

  it("draws nothing on the billing pages, which say it in full, and does not even ask", () => {
    mocks.path = "/billing/plans";
    const { container } = render(<BillingBanner />);
    expect(mocks.state).not.toHaveBeenCalled();
    expect(container.innerHTML).toBe("");
  });

  it("asks once per tab when billing is not available to this person (404), and draws nothing", async () => {
    mocks.state.mockRejectedValue(new ApiError(404, "not_found", "Not found."));
    const first = render(<BillingBanner />);
    await waitFor(() => expect(mocks.state).toHaveBeenCalledTimes(1));
    first.unmount();
    const second = render(<BillingBanner />);
    expect(mocks.state).toHaveBeenCalledTimes(1);
    expect(second.container.innerHTML).toBe("");
  });

  it("never turns a failed read into an error on the page", async () => {
    mocks.state.mockRejectedValue(new ApiError(502, "gateway_error", "slow"));
    const { container } = render(<BillingBanner />);
    await waitFor(() => expect(mocks.state).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });
});
