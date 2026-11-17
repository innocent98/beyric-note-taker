// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReturnView } from "@/components/billing/return-view";
import { ApiError, NETWORK_MESSAGE } from "@/lib/api/client";
import { cap } from "./billing-fixtures";

// The page the provider sends the person back to: it reads OUR payment and says "paid" only for `succeeded`.
// Bodies are the captured ones (17/18 pending, 25 paid); the failed, refunded and abandoned shapes are the same
// payment object with another `status`, which the guide documents but the capture run could not produce over HTTP.

const mocks = vi.hoisted(() => ({ payment: vi.fn(), state: vi.fn(), search: "" }));
vi.mock("@/lib/api/browser", () => ({ api: { billing: { payment: mocks.payment, state: mocks.state } } }));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(mocks.search) }));

const pending = cap<Record<string, unknown>>("18-payment-pending.json");
const paid = cap<Record<string, unknown>>("25-payment-paid.json");
const REF = pending.reference as string;

async function settle(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  window.sessionStorage.clear();
  mocks.search = `reference=${REF}&trxref=${REF}`;
});
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
  cleanup();
});

describe("while the payment is pending", () => {
  it("keeps asking every 2 seconds, then says paid only when it is succeeded", async () => {
    mocks.payment.mockResolvedValueOnce(pending).mockResolvedValueOnce(pending).mockResolvedValue(paid);
    render(<ReturnView />);
    await settle();
    expect(screen.getByText("Confirming your payment...")).toBeTruthy();
    expect(screen.queryByText("Payment received.")).toBeNull();
    await settle(2000);
    expect(mocks.payment).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("Payment received.")).toBeNull();
    await settle(2000);
    expect(screen.getByText("Payment received.")).toBeTruthy();
    expect(screen.getByText("Your plan is active and your hours are added.")).toBeTruthy();
    expect(screen.getByText("$18.00 for your plan.")).toBeTruthy();
    expect(mocks.payment).toHaveBeenCalledWith(REF);
    // and it stops asking
    await settle(60_000);
    expect(mocks.payment).toHaveBeenCalledTimes(3);
  });

  it("slows to 10 seconds after 30 seconds and stops at 5 minutes with the honest words", async () => {
    mocks.payment.mockResolvedValue(pending);
    render(<ReturnView />);
    await settle();
    await settle(30_000);
    const atThirty = mocks.payment.mock.calls.length;
    expect(atThirty).toBeGreaterThanOrEqual(15);
    await settle(20_000);
    expect(mocks.payment.mock.calls.length - atThirty).toBeLessThanOrEqual(3);
    await settle(300_000);
    expect(screen.getByText("We are still confirming your payment.")).toBeTruthy();
    expect(screen.getByText(/Your hours will appear when the payment is confirmed/)).toBeTruthy();
    const total = mocks.payment.mock.calls.length;
    await settle(120_000);
    expect(mocks.payment.mock.calls.length).toBe(total);
    expect(screen.getByRole("button", { name: "Check again" })).toBeTruthy();
    expect(screen.getByText(REF)).toBeTruthy();
  });
});

describe("the other states", () => {
  it("says a failed payment added no hours and offers to try again", async () => {
    mocks.payment.mockResolvedValue({ ...pending, status: "failed", pay_url: null });
    render(<ReturnView />);
    await settle();
    expect(screen.getByText("The payment did not go through.")).toBeTruthy();
    expect(screen.getByText(/No hours were added/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Try again" }).getAttribute("href")).toBe("/billing/plans");
  });

  it("says a person who backed out did not pay, keeps reading, and still shows a late success", async () => {
    mocks.search = `reference=${REF}&status=cancelled`;
    mocks.payment.mockResolvedValueOnce(pending).mockResolvedValue(paid);
    render(<ReturnView />);
    await settle();
    expect(screen.getByText("You did not finish paying.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go back to the payment page" }).getAttribute("href")).toBe(`https://fake.test/pay/${REF}`);
    await settle(2000);
    expect(screen.getByText("Payment received.")).toBeTruthy();
  });

  it("never trusts the address: a success shown by the provider's words is not shown without our payment", async () => {
    mocks.search = `reference=${REF}&status=successful`;
    mocks.payment.mockResolvedValue(pending);
    render(<ReturnView />);
    await settle();
    expect(screen.queryByText("Payment received.")).toBeNull();
    expect(screen.getByText("Confirming your payment...")).toBeTruthy();
  });

  it("says a refund was refunded", async () => {
    mocks.payment.mockResolvedValue({ ...paid, status: "refunded" });
    render(<ReturnView />);
    await settle();
    expect(screen.getByText("This payment was refunded.")).toBeTruthy();
  });

  it("says it cannot find a payment that is not theirs: the real route answers 404 not_found, and billing is on (the state answers)", async () => {
    mocks.payment.mockRejectedValue(new ApiError(404, "not_found", "Not found."));
    mocks.state.mockResolvedValue(cap("26-subscription-active.json"));
    render(<ReturnView />);
    await settle();
    expect(screen.getByText("We could not find that payment.")).toBeTruthy();
    expect(screen.queryByText("Billing is not available for your account.")).toBeNull();
    expect(mocks.payment).toHaveBeenCalledTimes(1);
  });

  it("keeps trying after a lost connection and says so", async () => {
    mocks.payment.mockRejectedValueOnce(new ApiError(0, "network_error", NETWORK_MESSAGE)).mockResolvedValue(paid);
    render(<ReturnView />);
    await settle();
    expect(screen.getByText(/Could not reach the server/)).toBeTruthy();
    await settle(2000);
    expect(screen.getByText("Payment received.")).toBeTruthy();
  });

  it("says nothing was named when the address has no payment and the tab remembers none", async () => {
    mocks.search = "";
    render(<ReturnView />);
    await settle();
    expect(screen.getByText("There is no payment to check here.")).toBeTruthy();
    expect(mocks.payment).not.toHaveBeenCalled();
  });

  it("falls back to the reference this tab sent the person to pay", async () => {
    mocks.search = "";
    window.sessionStorage.setItem("beyric.billing.reference", REF);
    mocks.payment.mockResolvedValue(paid);
    render(<ReturnView />);
    await settle();
    expect(mocks.payment).toHaveBeenCalledWith(REF);
    expect(screen.getByText("Payment received.")).toBeTruthy();
  });

  it("says billing is not available only when the state is a 404 too (billing off for this account)", async () => {
    mocks.payment.mockRejectedValue(new ApiError(404, "not_found", "Not found."));
    mocks.state.mockRejectedValue(new ApiError(404, "not_found", "Not found."));
    render(<ReturnView />);
    await settle();
    expect(screen.getByText("Billing is not available for your account.")).toBeTruthy();
  });

  it("stops after 5 minutes of lost connection, says so (not that it will try again) and offers Check again", async () => {
    mocks.payment.mockRejectedValue(new ApiError(0, "network_error", NETWORK_MESSAGE));
    render(<ReturnView />);
    await settle();
    expect(screen.getByText(/We will try again/)).toBeTruthy();
    await settle(310_000);
    expect(screen.getByText("We could not reach the server to check your payment.")).toBeTruthy();
    expect(screen.queryByText(/We will try again/)).toBeNull();
    const calls = mocks.payment.mock.calls.length;
    await settle(120_000);
    expect(mocks.payment.mock.calls.length).toBe(calls);
    mocks.payment.mockResolvedValue(paid);
    await act(async () => {
      screen.getByRole("button", { name: "Check again" }).click();
    });
    await settle();
    expect(screen.getByText("Payment received.")).toBeTruthy();
  });

  it("moves focus to the new answer and keeps one live region", async () => {
    mocks.payment.mockResolvedValueOnce(pending).mockResolvedValue(paid);
    const { container } = render(<ReturnView />);
    await settle();
    expect(container.querySelectorAll('[aria-live="polite"]').length).toBe(1);
    await settle(2000);
    expect(document.activeElement?.textContent).toBe("Payment received.");
    expect(container.querySelectorAll('[aria-live="polite"]').length).toBe(1);
  });
});
