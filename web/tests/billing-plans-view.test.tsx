// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PlansView } from "@/components/billing/plans-view";
import { ApiError } from "@/lib/api/client";
import { cap, PROFILE_NONE, PROFILE_US } from "./billing-fixtures";

// The plans screen (billing stage S8b, part A), on the bodies captured from the running app
// (docs/fe-captures/billing-subscriptions/).

const mocks = vi.hoisted(() => ({
  plans: vi.fn(),
  state: vi.fn(),
  profile: vi.fn(),
  checkout: vi.fn(),
  routeFor: vi.fn(),
  setCountry: vi.fn(),
  goTo: vi.fn(),
}));
vi.mock("@/lib/api/browser", () => ({
  api: { billing: { plans: mocks.plans, state: mocks.state, profile: mocks.profile, checkout: mocks.checkout, routeFor: mocks.routeFor, setCountry: mocks.setCountry } },
}));
vi.mock("@/lib/navigate", () => ({ goTo: mocks.goTo }));

beforeEach(() => {
  window.sessionStorage.clear();
  mocks.plans.mockResolvedValue(cap("12-plans-us.json"));
  mocks.state.mockResolvedValue(cap("09-subscription-never.json"));
  mocks.profile.mockResolvedValue(PROFILE_US);
});
afterEach(() => {
  vi.clearAllMocks();
  cleanup();
});

describe("a person with a country and no plan", () => {
  it("says what they pay in, lists the plans with the API's prices, and starts a checkout", async () => {
    mocks.checkout.mockResolvedValue(cap("17-checkout-plan.json"));
    render(<PlansView />);
    expect(await screen.findByText("20 hours a month")).toBeTruthy();
    expect(screen.getAllByText("Prices in US dollars, paid through Flutterwave.").length).toBeGreaterThan(0);
    expect(screen.getByText("$18.00")).toBeTruthy();
    expect(screen.getByText("$34.00")).toBeTruthy();
    await userEvent.click(screen.getAllByRole("button", { name: "Choose this plan" })[0]);
    await waitFor(() => expect(mocks.goTo).toHaveBeenCalledWith(cap<{ checkout_url: string }>("17-checkout-plan.json").checkout_url));
    expect(mocks.checkout).toHaveBeenCalledWith("plan", "plan_20h");
    // the reference is kept for the return page, in case the provider's address brings nothing back
    expect(window.sessionStorage.getItem("beyric.billing.reference")).toBe(cap<{ payment: { reference: string } }>("17-checkout-plan.json").payment.reference);
  });

  it("tells them what renewing, cancelling and a changed price mean before they pay", async () => {
    render(<PlansView />);
    await screen.findByText("Before you pay");
    expect(screen.getByText(/renews every month by charging the card you pay with/)).toBeTruthy();
    expect(screen.getByText(/The unused part of the month is not refunded/)).toBeTruthy();
    expect(screen.getByText(/do not carry over/)).toBeTruthy();
    expect(screen.getByText(/our price changes, it applies from your next renewal/)).toBeTruthy();
    expect(screen.getByText(/not when you come back to this site/)).toBeTruthy();
  });

  it("shows the server's sentence when the checkout is refused, and stays on the page", async () => {
    mocks.checkout.mockRejectedValue(new ApiError(502, "provider_error", "The payment page could not be opened. Try again in a moment."));
    render(<PlansView />);
    await userEvent.click((await screen.findAllByRole("button", { name: "Choose this plan" }))[0]);
    expect(await screen.findByText("The payment page could not be opened. Try again in a moment.")).toBeTruthy();
    expect(mocks.goTo).not.toHaveBeenCalled();
  });
});

describe("a person billed from Nigeria", () => {
  it("shows the naira price from the API, and lists a plan with no naira price as not on sale with its button off", async () => {
    // capture 69: plan_20h is priced in kobo; the capture's extra plan, capture_40h, has no naira price
    mocks.plans.mockResolvedValue(cap("69-plans-ng.json"));
    mocks.profile.mockResolvedValue({ ...PROFILE_US, billing_country: "NG", route: { currency: "NGN", provider: "paystack" } });
    render(<PlansView />);
    expect(await screen.findByText("20 hours a month")).toBeTruthy();
    expect(screen.getAllByText(/Prices in naira, paid through Paystack\./).length).toBeGreaterThan(0);
    expect(screen.getByText(/25,000\.00/)).toBeTruthy();
    expect(screen.getAllByText(/not on sale in naira yet/).length).toBe(1);
    const buttons = screen.getAllByRole("button", { name: "Choose this plan" }) as HTMLButtonElement[];
    expect(buttons.map((b) => b.disabled)).toEqual([false, true]);
  });

  it("lists every plan as not on sale when none has a naira price", async () => {
    const body = cap<{ plans: Array<Record<string, unknown>> }>("69-plans-ng.json");
    mocks.plans.mockResolvedValue({ ...body, plans: body.plans.map((plan) => ({ ...plan, price: null })), packs: [] });
    mocks.profile.mockResolvedValue({ ...PROFILE_US, billing_country: "NG", route: { currency: "NGN", provider: "paystack" } });
    render(<PlansView />);
    await screen.findByText("20 hours a month");
    expect(screen.getAllByText(/not on sale in naira yet/).length).toBe(2);
    for (const button of screen.getAllByRole("button", { name: "Choose this plan" })) expect((button as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("a person who has not chosen a billing country", () => {
  it("asks for the country first, shows no prices and cannot start a checkout", async () => {
    mocks.plans.mockResolvedValue(cap("08-plans-no-country.json"));
    mocks.profile.mockResolvedValue(PROFILE_NONE);
    render(<PlansView />);
    expect(await screen.findByText("Choose your billing country first. Prices are shown once we know what you pay in.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Choose this plan" })).toBeNull();
  });

  it("states the result of the picked country before it is saved, then saves it", async () => {
    mocks.plans.mockResolvedValue(cap("08-plans-no-country.json"));
    mocks.profile.mockResolvedValue(PROFILE_NONE);
    mocks.routeFor.mockResolvedValue({ currency: "NGN", provider: "paystack", billing_country: "NG" });
    mocks.setCountry.mockResolvedValue({ result: "applied", profile: { ...PROFILE_US, billing_country: "NG", route: { currency: "NGN", provider: "paystack" } } });
    render(<PlansView />);
    const select = await screen.findByRole("combobox", { name: "Billing country" });
    await userEvent.selectOptions(select, "NG");
    expect(await screen.findByText("Prices in naira, paid through Paystack.")).toBeTruthy();
    expect(mocks.routeFor).toHaveBeenCalledWith("NG");
    await userEvent.click(screen.getByRole("button", { name: "Save country" }));
    await waitFor(() => expect(mocks.setCountry).toHaveBeenCalledWith("NG"));
  });

  it("explains the change rules", async () => {
    mocks.profile.mockResolvedValue(PROFILE_NONE);
    render(<PlansView />);
    await screen.findByText(/once every 30 days/);
    expect(screen.getByText(/the change waits until your paid month ends/)).toBeTruthy();
  });
});

describe("a country that cannot be changed yet", () => {
  it("disables the change and says when, for a change inside 30 days", async () => {
    mocks.profile.mockResolvedValue({ ...PROFILE_US, country_change_allowed_at: "2999-01-01T00:00:00Z" });
    render(<PlansView />);
    const change = await screen.findByRole("button", { name: "Change country" });
    expect((change as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/You can change it again on/)).toBeTruthy();
  });

  it("offers no change while one waits for the end of the paid month, and says when", async () => {
    mocks.profile.mockResolvedValue({ ...PROFILE_US, pending_billing_country: "NG", pending_effective_at: "2999-01-01T00:00:00Z" });
    render(<PlansView />);
    expect(await screen.findByText(/changes to Nigeria on/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Change country" })).toBeNull();
  });
});

describe("a person who already has a plan", () => {
  it("sends them to Billing and turns the plan buttons off", async () => {
    mocks.state.mockResolvedValue(cap("26-subscription-active.json"));
    render(<PlansView />);
    expect(await screen.findByText(/You already have a plan/)).toBeTruthy();
    for (const button of screen.getAllByRole("button", { name: "Choose this plan" })) expect((button as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("a checkout that was started and not finished", () => {
  it("offers to continue on that payment's own page", async () => {
    mocks.state.mockResolvedValue(cap("19-subscription-pending.json"));
    render(<PlansView />);
    const link = await screen.findByRole("link", { name: "Continue to pay" });
    expect(link.getAttribute("href")).toBe(cap<{ checkout_url: string }>("17-checkout-plan.json").checkout_url);
  });
});

describe("billing that is not available to this person", () => {
  it("says so plainly (the routes answer 404 not_found)", async () => {
    mocks.plans.mockRejectedValue(new ApiError(404, "not_found", "Not found."));
    render(<PlansView />);
    expect(await screen.findByText("Plans are not available for your account yet")).toBeTruthy();
  });
});

describe("the plan button", () => {
  it("calls the checkout once however often it is pressed, and is freed when the page comes back from the cache", async () => {
    mocks.checkout.mockResolvedValue(cap("17-checkout-plan.json"));
    render(<PlansView />);
    const first = (await screen.findAllByRole("button", { name: "Choose this plan" }))[0];
    await userEvent.click(first);
    await userEvent.click(first);
    await waitFor(() => expect(mocks.goTo).toHaveBeenCalledTimes(1));
    expect(mocks.checkout).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Opening the payment page...")).toBeTruthy();
    act(() => {
      const event = new Event("pageshow") as Event & { persisted: boolean };
      event.persisted = true;
      window.dispatchEvent(event);
    });
    expect(screen.queryByText("Opening the payment page...")).toBeNull();
  });

  it("keeps the plans and shows the error above them when a re-read fails", async () => {
    mocks.profile.mockResolvedValue(PROFILE_NONE);
    mocks.plans.mockResolvedValue(cap("08-plans-no-country.json"));
    mocks.routeFor.mockResolvedValue({ currency: "USD", provider: "flutterwave", billing_country: "US" });
    mocks.setCountry.mockResolvedValue({ result: "applied", profile: PROFILE_US });
    render(<PlansView />);
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Billing country" }), "US");
    await screen.findByText("Prices in US dollars, paid through Flutterwave.");
    mocks.plans.mockRejectedValue(new ApiError(502, "gateway_error", "slow"));
    await userEvent.click(screen.getByRole("button", { name: "Save country" }));
    expect(await screen.findByText(/What you see may be out of date/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Billing country" })).toBeTruthy();
  });
});
