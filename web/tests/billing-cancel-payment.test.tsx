// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BillingView } from "@/components/billing/billing-view";
import { CancelPayment } from "@/components/billing/cancel-payment";
import { PlansView } from "@/components/billing/plans-view";
import { ApiError } from "@/lib/api/client";
import { cap, capCancel, PROFILE_US } from "./billing-fixtures";

// "Cancel this payment" next to "Continue to pay" and "Check this payment" (the notice never went away for a page the
// person closed). The answers are the captured ones (docs/fe-captures/billing-cancel-checkout/, the provider a scripted
// double): 16 cancelled, 20 paid after all, 24 failed, 14 the provider could not be asked, 13 not found.

const mocks = vi.hoisted(() => ({
  plans: vi.fn(),
  state: vi.fn(),
  profile: vi.fn(),
  payments: vi.fn(),
  checkout: vi.fn(),
  routeFor: vi.fn(),
  setCountry: vi.fn(),
  cancelPayment: vi.fn(),
  goTo: vi.fn(),
}));
vi.mock("@/lib/api/browser", () => ({ api: { billing: mocks } }));
vi.mock("@/lib/navigate", () => ({ goTo: mocks.goTo }));

const PENDING = () => cap<{ pending_payment: { reference: string; kind: string } }>("19-subscription-pending.json");
const NEVER = () => cap("09-subscription-never.json");
const CANCELLED = () => capCancel<{ result: string; payment: unknown }>("16-cancel-abandoned.json");
const PAID = () => capCancel<{ result: string; payment: unknown }>("20-cancel-paid-in-the-meantime.json");
const FAILED = () => capCancel<{ result: string; payment: unknown }>("24-cancel-provider-says-failed.json");
const PROVIDER_DOWN = () => {
  const { error } = capCancel<{ error: { code: string; message: string } }>("15-cancel-provider-down.json");
  return new ApiError(502, error.code, error.message);
};

// The first read shows the waiting payment; every read after that is what the server says once it was cancelled.
function thenShow(next: unknown) {
  mocks.state.mockReset();
  mocks.state.mockResolvedValueOnce(PENDING()).mockResolvedValue(next);
}

beforeEach(() => {
  window.sessionStorage.clear();
  mocks.plans.mockResolvedValue(cap("12-plans-us.json"));
  mocks.state.mockResolvedValue(PENDING());
  mocks.profile.mockResolvedValue(PROFILE_US);
  mocks.payments.mockResolvedValue([]);
});
afterEach(() => {
  vi.clearAllMocks();
  cleanup();
});

describe("the plans screen's unfinished-payment notice", () => {
  it("offers Cancel this payment beside Continue to pay and Check this payment", async () => {
    render(<PlansView />);
    expect(await screen.findByRole("link", { name: "Continue to pay" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Check this payment" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel this payment" })).toBeTruthy();
  });

  it("cancels that payment, reads the state again, and the notice goes away", async () => {
    mocks.cancelPayment.mockResolvedValue(CANCELLED());
    thenShow(NEVER()); // what the server says after: nothing is waiting
    render(<PlansView />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel this payment" }));
    expect(mocks.cancelPayment).toHaveBeenCalledWith(PENDING().pending_payment.reference);
    await waitFor(() => expect(screen.queryByText(/You started a payment and have not finished it/)).toBeNull());
    expect(screen.getByText(/The payment was cancelled and no hours were added\. Please do not pay on the old payment page\./)).toBeTruthy();
    expect(mocks.state.mock.calls.length).toBeGreaterThan(1);
  });

  it("lets the person dismiss the notice", async () => {
    mocks.cancelPayment.mockResolvedValue(CANCELLED());
    thenShow(NEVER());
    render(<PlansView />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel this payment" }));
    expect(await screen.findByText(/The payment was cancelled and no hours were added/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/The payment was cancelled and no hours were added/)).toBeNull();
  });

  it("shows the busy state while it asks, and cannot be pressed twice", async () => {
    let finish: (value: unknown) => void = () => {};
    mocks.cancelPayment.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    thenShow(NEVER());
    render(<PlansView />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel this payment" }));
    const busy = (await screen.findByRole("button", { name: "Cancelling..." })) as HTMLButtonElement;
    expect(busy.disabled).toBe(true);
    await userEvent.click(busy);
    expect(mocks.cancelPayment).toHaveBeenCalledTimes(1);
    finish(CANCELLED());
    await waitFor(() => expect(screen.queryByRole("button", { name: "Cancelling..." })).toBeNull());
  });

  it("says a page that was paid after all was NOT cancelled, and shows what the server holds", async () => {
    mocks.cancelPayment.mockResolvedValue(PAID());
    thenShow(cap("26-subscription-active.json"));
    render(<PlansView />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel this payment" }));
    expect(await screen.findByText(/That payment had already gone through, so it was not cancelled/)).toBeTruthy();
    expect(screen.queryByText(/The payment was cancelled/)).toBeNull();
  });

  it("treats a payment the provider says failed like a cancelled one", async () => {
    mocks.cancelPayment.mockResolvedValue(FAILED());
    thenShow(NEVER());
    render(<PlansView />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel this payment" }));
    expect(await screen.findByText(/The payment was cancelled and no hours were added/)).toBeTruthy();
  });

  it("keeps the notice and the button, and says why, when the provider could not be asked (nothing was changed)", async () => {
    mocks.cancelPayment.mockRejectedValue(PROVIDER_DOWN());
    render(<PlansView />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel this payment" }));
    expect(await screen.findByText(/We could not check this payment with the payment provider just now\. Nothing was changed\./)).toBeTruthy();
    expect(screen.getByText(/You started a payment and have not finished it/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Cancel this payment" }) as HTMLButtonElement).disabled).toBe(false);
    expect(mocks.state).toHaveBeenCalledTimes(1); // no reload: the state did not change
  });

  it("forgets the reference this tab kept for the return page", async () => {
    const reference = PENDING().pending_payment.reference;
    window.sessionStorage.setItem("beyric.billing.reference", reference);
    mocks.cancelPayment.mockResolvedValue(CANCELLED());
    render(<PlansView />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel this payment" }));
    await waitFor(() => expect(window.sessionStorage.getItem("beyric.billing.reference")).toBeNull());
  });
});

describe("the billing page's unfinished-payment notice", () => {
  it("has the same button and the same notice afterwards", async () => {
    mocks.cancelPayment.mockResolvedValue(CANCELLED());
    thenShow(NEVER());
    render(<BillingView />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel this payment" }));
    expect(mocks.cancelPayment).toHaveBeenCalledWith(PENDING().pending_payment.reference);
    await waitFor(() => expect(screen.queryByText(/You have a payment that is not finished/)).toBeNull());
    expect(screen.getByText(/The payment was cancelled and no hours were added/)).toBeTruthy();
  });
});

describe("what can be cancelled", () => {
  it("offers nothing for a renewal or an upgrade (the server refuses them: 409 cannot_cancel)", () => {
    for (const kind of ["renewal", "upgrade"] as const) {
      const { container } = render(<CancelPayment payment={{ reference: "ref", kind }} onDone={() => {}} />);
      expect(container.textContent).toBe("");
      cleanup();
    }
  });

  it("just reads again, saying nothing, when the server no longer knows the payment (404)", async () => {
    const done = vi.fn();
    mocks.cancelPayment.mockRejectedValue(new ApiError(404, "not_found", "Not found."));
    render(<CancelPayment payment={{ reference: "ref", kind: "plan" }} onDone={done} />);
    await userEvent.click(screen.getByRole("button", { name: "Cancel this payment" }));
    await waitFor(() => expect(done).toHaveBeenCalledWith(null));
  });
});
