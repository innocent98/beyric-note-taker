// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "@testing-library/react";
import { BillingView } from "@/components/billing/billing-view";
import { SettingsBilling } from "@/components/billing/settings-billing";
import { ApiError } from "@/lib/api/client";
import { cap, PROFILE_US } from "./billing-fixtures";

// The billing page (billing stage S8b, part B) on the bodies captured from the running app.

const mocks = vi.hoisted(() => ({
  state: vi.fn(),
  plans: vi.fn(),
  payments: vi.fn(),
  profile: vi.fn(),
  cancel: vi.fn(),
  resume: vi.fn(),
  previewChange: vi.fn(),
  changePlan: vi.fn(),
  checkout: vi.fn(),
  payLink: vi.fn(),
  routeFor: vi.fn(),
  setCountry: vi.fn(),
  goTo: vi.fn(),
}));
vi.mock("@/lib/api/browser", () => ({ api: { billing: mocks } }));
vi.mock("@/lib/navigate", () => ({ goTo: mocks.goTo }));

type Json = Record<string, unknown>;
const active = cap<Json>("26-subscription-active.json");
const sub = (over: Json) => ({ ...(active.subscription as Json), ...over });
const plans = cap<Json>("12-plans-us.json");
const history = cap<{ payments: unknown[] }>("63-payments-list.json").payments;

function show(state: Json, over: { payments?: unknown[]; plans?: Json } = {}) {
  mocks.state.mockResolvedValue(state);
  mocks.plans.mockResolvedValue(over.plans ?? plans);
  mocks.payments.mockResolvedValue(over.payments ?? []);
  mocks.profile.mockResolvedValue(PROFILE_US);
}
const card = (title: string) => screen.getByRole("heading", { name: title }).closest("section") as HTMLElement;

beforeEach(() => show(active));
afterEach(() => {
  vi.clearAllMocks();
  cleanup();
});

describe("an active plan", () => {
  it("shows the plan, its status, the renewal date and the next charge, and the hours", async () => {
    render(<BillingView />);
    expect(await screen.findByText("20 hours a month")).toBeTruthy();
    expect(screen.getByText("Active")).toBeTruthy();
    expect(within(card("Your plan")).getByText(/Your plan renews on/)).toBeTruthy();
    expect(within(card("Your plan")).getByText("$18.00")).toBeTruthy();
    expect(within(card("Your hours")).getByText("20 hours left")).toBeTruthy();
    expect(within(card("Your hours")).getByText(/do not carry over/)).toBeTruthy();
  });

  it("says a price that changed at renewal, and does not when it did not", async () => {
    show({ ...active, subscription: sub({ amount_minor: 2100 }) }, { payments: [{ ...history.at(-1) as Json }] });
    render(<BillingView />);
    expect(await screen.findByText(/That is not the \$18\.00 you paid last time: the price changed/)).toBeTruthy();
    cleanup();
    show(active, { payments: [{ ...history.at(-1) as Json }] });
    render(<BillingView />);
    await screen.findByText("20 hours a month");
    expect(screen.queryByText(/the price changed/)).toBeNull();
  });

  it("asks before cancelling and says what cancelling means, then cancels", async () => {
    mocks.cancel.mockResolvedValue(cap("36-cancel.json"));
    render(<BillingView />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel plan" }));
    expect(screen.getByText(/It keeps working until/)).toBeTruthy();
    expect(screen.getByText(/does not refund the part of this month you do not use/)).toBeTruthy();
    expect(screen.getByText(/you can take the cancel back/)).toBeTruthy();
    expect(mocks.cancel).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Yes, cancel my plan" }));
    await waitFor(() => expect(mocks.cancel).toHaveBeenCalledTimes(1));
  });

  it("can keep the plan after opening the cancel question", async () => {
    render(<BillingView />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel plan" }));
    await userEvent.click(within(screen.getByText(/Cancel your plan\?/).closest(".alert") as HTMLElement).getByRole("button", { name: "Keep my plan" }));
    expect(mocks.cancel).not.toHaveBeenCalled();
    expect(screen.queryByText(/Cancel your plan\?/)).toBeNull();
  });

  it("shows only the buttons the can_* flags allow", async () => {
    show({ ...active, subscription: sub({ can_cancel: false, can_change_plan: false }) });
    render(<BillingView />);
    await screen.findByText("20 hours a month");
    expect(screen.queryByRole("button", { name: "Cancel plan" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Change plan" })).toBeNull();
  });
});

describe("a cancelled plan that still runs", () => {
  it("says the plan ends, that nothing more is charged, and can be taken back", async () => {
    show({ ...active, subscription: cap<Json>("36-cancel.json") });
    mocks.resume.mockResolvedValue(cap("38-resume.json"));
    render(<BillingView />);
    expect(await screen.findByText("Ending")).toBeTruthy();
    expect(screen.getByText(/You will not be/)).toBeTruthy();
    expect(screen.getByText(/does not refund the part of this month you do not use/)).toBeTruthy();
    // keeping the plan says what will be charged and when
    expect(screen.getByText(/If you keep your plan, your card\s+is charged/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Keep my plan" }));
    await waitFor(() => expect(mocks.resume).toHaveBeenCalledTimes(1));
  });
});

describe("changing plan", () => {
  it("shows the price and the hours BEFORE an upgrade is charged, then goes to the checkout", async () => {
    mocks.previewChange.mockResolvedValue(cap("41-preview-upgrade.json"));
    mocks.changePlan.mockResolvedValue(cap("42-change-upgrade.json"));
    render(<BillingView />);
    await userEvent.click(await screen.findByRole("button", { name: "See what changes" }));
    expect(mocks.previewChange).toHaveBeenCalledWith("capture_40h");
    expect(await screen.findByText(/You pay \$15\.99 today/)).toBeTruthy();
    expect(screen.getByText(/19 hours 54 minutes extra this month/)).toBeTruthy();
    expect(screen.getByText(/You are charged as soon as you pay on the provider's page, which shows the exact amount/)).toBeTruthy();
    expect(mocks.changePlan).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Confirm and pay $15.99" }));
    await waitFor(() => expect(mocks.goTo).toHaveBeenCalledWith(cap<{ checkout_url: string }>("42-change-upgrade.json").checkout_url));
  });

  it("says a downgrade charges nothing now and starts at the next renewal", async () => {
    show({ ...active, subscription: sub({ plan_code: "capture_40h", plan_name: "40 hours a month", amount_minor: 3400 }) });
    mocks.previewChange.mockResolvedValue(cap("45-preview-downgrade.json"));
    mocks.changePlan.mockResolvedValue(cap("46-change-downgrade.json"));
    render(<BillingView />);
    await userEvent.click(await screen.findByRole("button", { name: "See what changes" }));
    expect(await screen.findByText("Nothing is charged now.")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Move to 20 hours a month at my next renewal" }));
    await waitFor(() => expect(mocks.changePlan).toHaveBeenCalledWith("plan_20h"));
    expect(mocks.goTo).not.toHaveBeenCalled();
  });

  it("shows a waiting downgrade and takes it back", async () => {
    show({ ...active, subscription: cap<Json>("46-change-downgrade.json").subscription });
    mocks.changePlan.mockResolvedValue(cap("47-change-downgrade-cleared.json"));
    render(<BillingView />);
    expect(await screen.findByText(/You move to/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Keep 40 hours a month" }));
    await waitFor(() => expect(mocks.changePlan).toHaveBeenCalledWith("capture_40h"));
  });

  it("lists a plan with no price in this currency as not on sale and does not hide it", async () => {
    show(active, { plans: { ...plans, plans: [(plans.plans as Json[])[0], { ...(plans.plans as Json[])[1], price: null }] } });
    render(<BillingView />);
    expect(await screen.findByText("Not on sale in US dollars yet")).toBeTruthy();
  });

  it("shows the server's sentence when the change is refused", async () => {
    mocks.previewChange.mockRejectedValue(new ApiError(409, "payment_overdue", "A plan cannot be changed while a payment is overdue."));
    render(<BillingView />);
    await userEvent.click(await screen.findByRole("button", { name: "See what changes" }));
    expect(await screen.findByText("A plan cannot be changed while a payment is overdue.")).toBeTruthy();
  });
});

describe("a plan in its grace", () => {
  it("explains the grace, offers Pay now, hides the plan change and keeps Cancel and packs", async () => {
    show(cap("50-subscription-past-due.json"));
    mocks.payLink.mockResolvedValue(cap("52-pay-link.json"));
    render(<BillingView />);
    expect(await screen.findByText("Payment overdue")).toBeTruthy();
    expect(within(card("Your plan")).getByText(/No new hours are added until you pay/)).toBeTruthy();
    expect(within(card("Your plan")).getByText(/We will try your card again on/)).toBeTruthy();
    expect(within(card("Your hours")).getByText(/No new hours are added until your payment goes through/)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Change plan" })).toBeNull();
    expect(screen.getByText(/A plan cannot be changed while a payment is overdue/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel plan" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Buy these hours" })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Pay now" }));
    await waitFor(() => expect(mocks.goTo).toHaveBeenCalledWith(cap<{ checkout_url: string }>("52-pay-link.json").checkout_url));
  });

  it("sends a charge waiting for the bank to its own page, not to a second checkout", async () => {
    show(cap("57-subscription-3ds.json"));
    render(<BillingView />);
    const link = await screen.findByRole("link", { name: "Finish paying" });
    expect(link.getAttribute("href")).toBe("https://fake.test/3ds/open-charge");
    expect(mocks.payLink).not.toHaveBeenCalled();
  });
});

describe("a plan that is over, and a person who never had one", () => {
  it("says a lapsed plan ended, keeps notes and exports, and offers a plan", async () => {
    show(cap("61-subscription-lapsed-after-pass.json"));
    render(<BillingView />);
    expect(await screen.findByText("You do not have a plan.")).toBeTruthy();
    expect(screen.getByText(/ended because a payment was not made/)).toBeTruthy();
    expect(screen.getByText(/You can still read, search and export your notes/)).toBeTruthy();
    expect(screen.queryByText(/stay as they are|still here/)).toBeNull();
    // no date: the end of a lapsed plan is 3 days after the due date, which the page does not know
    expect(screen.queryByText(/ended on/)).toBeNull();
    expect(screen.getAllByRole("link", { name: "Choose a plan" })[0].getAttribute("href")).toBe("/billing/plans");
    expect(within(card("Your hours")).getByText(/Ask Beyric, translating and rewriting notes are closed/)).toBeTruthy();
    expect(within(card("Your hours")).getByText(/meetings your note taker would join from your calendar/)).toBeTruthy();
  });

  it("offers a plan to a person who never had one, and no extra hours without a plan", async () => {
    show(cap("09-subscription-never.json"));
    render(<BillingView />);
    expect(await screen.findByText("You do not have a plan.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Buy these hours" })).toBeNull();
    expect(within(card("Extra hours")).getByText(/You need a plan to buy extra hours/)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Change plan" })).toBeNull();
  });

  it("shows an unfinished checkout with its own page", async () => {
    show(cap("19-subscription-pending.json"));
    render(<BillingView />);
    expect(await screen.findByText(/You have a payment that is not finished/)).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "Continue to pay" })[0].getAttribute("href")).toBe(cap<{ checkout_url: string }>("17-checkout-plan.json").checkout_url);
  });
});

describe("the hours", () => {
  it("warns at 80% and at 100% in words, driven by `warning`", async () => {
    show({ ...active, hours: { seconds_left: 7200, holdings_seconds: 72000, warning: "low" } });
    render(<BillingView />);
    expect(await screen.findByText(/You have used most of your hours/)).toBeTruthy();
    expect(within(card("Your hours")).getByText("2 hours left")).toBeTruthy();
    cleanup();
    show({ ...active, hours: { seconds_left: 0, holdings_seconds: 72000, warning: "out" } });
    render(<BillingView />);
    expect(await screen.findByText(/You have used all of your hours, so new recordings will not start/)).toBeTruthy();
    expect(screen.getByText(/A meeting that is already running finishes/)).toBeTruthy();
    expect(screen.getByText(/Ask Beyric, translating and rewriting notes keep working/)).toBeTruthy();
  });
});

describe("extra hours and the payments", () => {
  it("sells a pack at the API's price and goes to its checkout", async () => {
    mocks.checkout.mockResolvedValue(cap("33-checkout-pack.json"));
    render(<BillingView />);
    expect(await screen.findByText(/\$10\.00 once/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Buy these hours" }));
    await waitFor(() => expect(mocks.checkout).toHaveBeenCalledWith("pack", "pack_10h"));
    expect(mocks.goTo).toHaveBeenCalledWith(cap<{ checkout_url: string }>("33-checkout-pack.json").checkout_url);
  });

  it("lists payments with their words, and offers a waiting payment's own page", async () => {
    show(active, { payments: history });
    render(<BillingView />);
    await screen.findByText("20 hours a month");
    const list = card("Payments");
    expect(within(list).getAllByText("Paid").length).toBe(4);
    expect(within(list).getAllByText("Did not go through").length).toBe(1);
    expect(within(list).getAllByText("Waiting").length).toBe(2);
    expect(within(list).getAllByText("Monthly renewal").length).toBe(3);
    expect(within(list).getAllByText("Extra hours").length).toBe(2);
    expect(within(list).getAllByRole("link", { name: "Continue to pay" }).length).toBe(2);
  });
});

describe("when billing is not available", () => {
  it("says so plainly on the page", async () => {
    mocks.state.mockRejectedValue(new ApiError(404, "not_found", "Not found."));
    render(<BillingView />);
    expect(await screen.findByText("Billing is not available for your account yet")).toBeTruthy();
  });

  it("shows a failed load as an error with Try again", async () => {
    mocks.state.mockRejectedValueOnce(new ApiError(502, "gateway_error", "x"));
    render(<BillingView />);
    expect(await screen.findByRole("button", { name: "Try again" })).toBeTruthy();
  });
});

describe("the Settings card", () => {
  it("draws nothing when billing is not available", async () => {
    mocks.state.mockRejectedValue(new ApiError(404, "not_found", "Not found."));
    const { container } = render(<SettingsBilling />);
    await waitFor(() => expect(mocks.state).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });

  it("summarises an active plan and links into Billing", async () => {
    render(<SettingsBilling />);
    expect(await screen.findByText("20 hours a month")).toBeTruthy();
    expect(screen.getByText(/Renews on/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open billing" }).getAttribute("href")).toBe("/billing");
    expect(screen.queryByRole("link", { name: "Choose a plan" })).toBeNull();
  });

  it("offers a plan to a person without one", async () => {
    mocks.state.mockResolvedValue(cap("09-subscription-never.json"));
    render(<SettingsBilling />);
    expect(await screen.findByText("You do not have a plan")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Choose a plan" }).getAttribute("href")).toBe("/billing/plans");
  });
});

// A promise a test settles by hand, to hold a call in flight while the button is pressed again.
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("a money button pressed twice calls once", () => {
  it("cancel", async () => {
    const held = deferred<unknown>();
    mocks.cancel.mockReturnValue(held.promise);
    render(<BillingView />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel plan" }));
    const yes = screen.getByRole("button", { name: "Yes, cancel my plan" });
    await userEvent.click(yes);
    await userEvent.click(yes);
    expect(mocks.cancel).toHaveBeenCalledTimes(1);
    expect((yes as HTMLButtonElement).disabled).toBe(true);
    held.resolve(cap("36-cancel.json"));
  });

  it("buying extra hours, and the button stays busy while the browser leaves", async () => {
    mocks.checkout.mockResolvedValue(cap("33-checkout-pack.json"));
    render(<BillingView />);
    const buy = await screen.findByRole("button", { name: "Buy these hours" });
    await userEvent.click(buy);
    await userEvent.click(buy);
    await waitFor(() => expect(mocks.goTo).toHaveBeenCalledTimes(1));
    expect(mocks.checkout).toHaveBeenCalledTimes(1);
    expect((screen.getByRole("button", { name: "Opening the payment page..." }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("confirming an upgrade: the button stays disabled after the call succeeds, while the browser leaves", async () => {
    mocks.previewChange.mockResolvedValue(cap("41-preview-upgrade.json"));
    mocks.changePlan.mockResolvedValue(cap("42-change-upgrade.json"));
    render(<BillingView />);
    await userEvent.click(await screen.findByRole("button", { name: "See what changes" }));
    const confirm = await screen.findByRole("button", { name: "Confirm and pay $15.99" });
    await userEvent.click(confirm);
    await userEvent.click(confirm);
    await waitFor(() => expect(mocks.goTo).toHaveBeenCalledTimes(1));
    expect(mocks.changePlan).toHaveBeenCalledTimes(1);
    expect((screen.getByRole("button", { name: "Opening the payment page..." }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("pay now in the grace", async () => {
    show(cap("50-subscription-past-due.json"));
    mocks.payLink.mockResolvedValue(cap("52-pay-link.json"));
    render(<BillingView />);
    const pay = await screen.findByRole("button", { name: "Pay now" });
    await userEvent.click(pay);
    await userEvent.click(pay);
    await waitFor(() => expect(mocks.goTo).toHaveBeenCalledTimes(1));
    expect(mocks.payLink).toHaveBeenCalledTimes(1);
  });
});

describe("coming back with the browser's back button", () => {
  it("frees a button that was left busy (a page restored from the back/forward cache)", async () => {
    mocks.checkout.mockResolvedValue(cap("33-checkout-pack.json"));
    render(<BillingView />);
    await userEvent.click(await screen.findByRole("button", { name: "Buy these hours" }));
    await waitFor(() => expect(mocks.goTo).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "Buy these hours" })).toBeNull();
    act(() => {
      const event = new Event("pageshow") as Event & { persisted: boolean };
      event.persisted = true;
      window.dispatchEvent(event);
    });
    expect((screen.getByRole("button", { name: "Buy these hours" }) as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("a re-read that fails", () => {
  it("keeps the page and shows the error above it, with Try again", async () => {
    render(<BillingView />);
    await screen.findByText("20 hours a month");
    mocks.state.mockRejectedValue(new ApiError(502, "gateway_error", "slow"));
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    expect(await screen.findByText(/What you see may be out of date/)).toBeTruthy();
    expect(screen.getByText("20 hours a month")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel plan" })).toBeTruthy();
  });
});

describe("the cancel question", () => {
  it("is announced (role alert) and takes focus", async () => {
    render(<BillingView />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel plan" }));
    expect(screen.getByRole("alert").textContent).toMatch(/Cancel your plan\?/);
    expect(document.activeElement?.textContent).toBe("Cancel your plan?");
  });

  it("shows the server's sentence when the cancel is refused", async () => {
    mocks.cancel.mockRejectedValue(new ApiError(409, "no_subscription", "You do not have a plan."));
    render(<BillingView />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel plan" }));
    await userEvent.click(screen.getByRole("button", { name: "Yes, cancel my plan" }));
    expect(await screen.findByText("You do not have a plan.")).toBeTruthy();
  });
});

describe("an upgrade preview the API could not price", () => {
  it("says so and offers Try again instead of a dead end", async () => {
    mocks.previewChange.mockResolvedValue({ ...cap<Json>("41-preview-upgrade.json"), amount_minor: null });
    render(<BillingView />);
    await userEvent.click(await screen.findByRole("button", { name: "See what changes" }));
    expect(await screen.findByText(/We could not work out the price of this change/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Confirm and pay/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(mocks.previewChange).toHaveBeenCalledTimes(2);
  });
});

describe("the cancel question during the grace", () => {
  it("says the hours work until the grace ends, that it cannot be undone and that the card is no longer tried", async () => {
    show(cap("50-subscription-past-due.json"));
    mocks.cancel.mockResolvedValue(cap("36-cancel.json"));
    render(<BillingView />);
    await userEvent.click(await screen.findByRole("button", { name: "Cancel plan" }));
    const question = screen.getByRole("alert");
    expect(question.textContent).toMatch(/Your remaining hours work until Fri 20 Nov 2026/);
    expect(question.textContent).toMatch(/This cannot be\s+undone, and we stop trying your card/);
    expect(question.textContent).not.toMatch(/take the cancel back/);
    expect(question.textContent).not.toMatch(/keeps working until/);
  });
});

describe("a price that moved between the page and the checkout", () => {
  it("does not send the person to pay a different pack price, says so, and refreshes", async () => {
    const started = cap<{ payment: Record<string, unknown>; checkout_url: string }>("33-checkout-pack.json");
    mocks.checkout.mockResolvedValue({ ...started, payment: { ...started.payment, amount_minor: 1200 } });
    render(<BillingView />);
    await userEvent.click(await screen.findByRole("button", { name: "Buy these hours" }));
    expect(await screen.findByText(/The price of these hours is now \$12\.00, not the \$10\.00 shown\. Nothing was charged/)).toBeTruthy();
    expect(mocks.goTo).not.toHaveBeenCalled();
    await waitFor(() => expect(mocks.state.mock.calls.length).toBeGreaterThan(1));
    expect((screen.getByRole("button", { name: "Buy these hours" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("does not send the person to pay a different upgrade amount than the preview, and shows the new one to confirm", async () => {
    mocks.previewChange.mockResolvedValue(cap("41-preview-upgrade.json"));
    const done = cap<{ payment: Record<string, unknown> }>("42-change-upgrade.json");
    mocks.changePlan.mockResolvedValue({ ...done, payment: { ...done.payment, amount_minor: 1700 } });
    render(<BillingView />);
    await userEvent.click(await screen.findByRole("button", { name: "See what changes" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirm and pay $15.99" }));
    expect(await screen.findByText(/The price of this change is now \$17\.00, not the \$15\.99 shown/)).toBeTruthy();
    expect(mocks.goTo).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Confirm and pay $17.00" })).toBeTruthy();
  });
});

describe("the lapsed plan card", () => {
  it("offers one way to pay and says not to do both when a renewal charge still waits", async () => {
    show(cap("61-subscription-lapsed-after-pass.json"));
    render(<BillingView />);
    expect(await screen.findByText(/Do one of the two, not both/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Finish paying" })).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "Choose a plan" }).length).toBeGreaterThan(0);
  });
});
