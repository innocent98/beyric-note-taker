// What the billing screens read and say. The shapes are the ones captured from the running app and
// pasted in docs/fe-integration-guide-billing.md ("Subscriptions, packs and the lapse") and
// docs/fe-integration-guide-billing-profile.md; the billing routes are not in docs/openapi.json, so
// they are written here and not generated. Amounts are whole minor units (cents, kobo), never
// decimals. No price is written in this file: every number a person reads comes from GET /plans, the
// subscription or a payment.

export type BillingRoute = { currency: string; provider: string };
export type Price = { currency: string; amount_minor: number };
export type PlanOffer = { code: string; name: string; monthly_seconds: number; price: Price | null };
export type PackOffer = { code: string; name: string; seconds: number; valid_months: number; price: Price };
export type Plans = { route: BillingRoute | null; plans: PlanOffer[]; packs: PackOffer[] };

export type SubscriptionStatus = "active" | "past_due" | "lapsed" | "canceled" | "ended";
// What the PERSON may do now (`trial`, `pilot`, `active`, `past_due`, `lapsed`, `none`); a different
// vocabulary from the subscription row's `status`.
export type PersonState = "trial" | "pilot" | "active" | "past_due" | "lapsed" | "none";

export type Subscription = {
  id: number;
  status: SubscriptionStatus;
  plan_code: string;
  plan_name: string;
  currency: string;
  // What the NEXT renewal charges (the plan they will be on, at today's price), not what they last paid.
  amount_minor: number;
  current_period_start: string;
  current_period_end: string;
  cancel_at_period_end: boolean;
  pending_plan_code: string | null;
  grace_ends_at: string | null;
  next_retry_at: string | null;
  can_cancel: boolean;
  can_resume: boolean;
  can_change_plan: boolean;
  can_pay_now: boolean;
};

export type BillingHours = {
  seconds_left: number | null;
  holdings_seconds: number | null;
  warning: "low" | "out" | null;
};

export type PaymentKind = "plan" | "renewal" | "upgrade" | "pack";
export type PaymentStatus = "pending" | "succeeded" | "failed" | "refunded" | "partly_refunded";

export type BillingPayment = {
  reference: string;
  kind: PaymentKind;
  status: PaymentStatus;
  currency: string;
  amount_minor: number;
  created_at: string;
  paid_at: string | null;
  // The payer's own page for a payment that is waiting for them; null once it is paid or failed.
  pay_url: string | null;
};

export type BillingState = {
  state: PersonState;
  hours: BillingHours;
  subscription: Subscription | null;
  pending_payment: BillingPayment | null;
};

export type Checkout = { payment: BillingPayment; checkout_url: string };

export type ChangePreview = {
  kind: "upgrade" | "downgrade" | "clear";
  plan_code: string;
  currency: string;
  amount_minor: number | null;
  extra_seconds: number | null;
  takes_effect: "now" | "next_renewal";
};

export type ChangeResult = {
  result: "checkout" | "scheduled" | "cleared";
  subscription: Subscription;
  payment: BillingPayment | null;
  checkout_url: string | null;
};

export type BillingProfile = {
  name_or_company: string | null;
  address: string | null;
  tax_id: string | null;
  billing_email: string | null;
  billing_country: string | null;
  pending_billing_country: string | null;
  pending_effective_at: string | null;
  route: BillingRoute | null;
  country_change_allowed_at: string | null;
};

export type CountryResult = { result: "applied" | "scheduled" | "unchanged"; profile: BillingProfile };
// GET /api/billing/route?country=XX: the same two words as the profile's `route`, but flat.
export type RoutePreview = { currency: string; provider: string; billing_country: string };

// ---- words ----

const CURRENCY_WORDS: Record<string, string> = { NGN: "naira", USD: "US dollars" };
const PROVIDER_NAMES: Record<string, string> = { paystack: "Paystack", flutterwave: "Flutterwave" };

export function currencyWords(currency: string): string {
  return CURRENCY_WORDS[currency] ?? currency;
}

export function providerName(provider: string): string {
  return PROVIDER_NAMES[provider] ?? provider.charAt(0).toUpperCase() + provider.slice(1);
}

// The plain statement before any payment (spec 5.2): read from the route, never assumed from the country.
export function routeStatement(route: BillingRoute): string {
  return `Prices in ${currencyWords(route.currency)}, paid through ${providerName(route.provider)}.`;
}

// An amount of the API's own, written for a person: "₦150,000.00", "$18.00". `amount_minor` is cents or kobo.
export function formatMoney(amountMinor: number, currency: string): string {
  const locale = currency === "NGN" ? "en-NG" : "en-US";
  try {
    return new Intl.NumberFormat(locale, { style: "currency", currency }).format(amountMinor / 100);
  } catch {
    return `${(amountMinor / 100).toFixed(2)} ${currency}`;
  }
}

// Hours as a person says them: "20 hours", "1 hour 30 minutes", "12 minutes", "less than a minute", "0 minutes". Under
// an hour it is minutes (a decimal like "0.2 hours" tells nobody anything, and "0 hours left" must never be shown
// while recording still works).
export function formatHours(seconds: number): string {
  const total = Math.max(Math.round(seconds / 60), 0);
  if (total === 0) return seconds > 0 ? "less than a minute" : "0 minutes";
  const unit = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
  if (total < 60) return unit(total, "minute");
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  return minutes === 0 ? unit(hours, "hour") : `${unit(hours, "hour")} ${unit(minutes, "minute")}`;
}

export const PAYMENT_KIND_WORDS: Record<PaymentKind, string> = {
  plan: "Plan",
  renewal: "Monthly renewal",
  upgrade: "Plan upgrade",
  pack: "Extra hours",
};

export const PAYMENT_STATUS_WORDS: Record<PaymentStatus, { label: string; tone: "success" | "warning" | "danger" | "neutral" }> = {
  succeeded: { label: "Paid", tone: "success" },
  pending: { label: "Waiting", tone: "warning" },
  failed: { label: "Did not go through", tone: "danger" },
  refunded: { label: "Refunded", tone: "neutral" },
  partly_refunded: { label: "Partly refunded", tone: "neutral" },
};

// The payment a checkout made must be for the price the screen showed. The server is the authority and the
// provider's page shows the real amount, but if the plan list was stale (another tab, a price or country change)
// the screen must not send the person off to pay a different amount without saying so.
export function samePrice(payment: Pick<BillingPayment, "currency" | "amount_minor">, price: Price): boolean {
  return payment.currency === price.currency && payment.amount_minor === price.amount_minor;
}

// An upgrade is prorated by the server to the second, so the amount made at the confirm is a little LOWER than the
// preview's (about one minor unit every second or two in kobo) and that is fine. Only a HIGHER amount, or another
// currency, stops the redirect.
export function notDearer(payment: Pick<BillingPayment, "currency" | "amount_minor">, shown: Price): boolean {
  return payment.currency === shown.currency && payment.amount_minor <= shown.amount_minor;
}

// ---- the return page ----

// Where the reference of the payment the person comes back to can be: Paystack adds `reference` and
// `trxref`, Flutterwave adds `tx_ref` (its names are not verified live, so all three are read) and a
// `status`. Nothing in the address says "paid": it is only ever used to find OUR payment.
export function referenceFrom(params: URLSearchParams): string | null {
  for (const name of ["reference", "trxref", "tx_ref"]) {
    const value = params.get(name)?.trim();
    if (value && value.length <= 100 && /^[A-Za-z0-9._-]+$/.test(value)) return value;
  }
  return null;
}

// The provider says the payer backed out. Only a hint for the words: the payment is still read from us.
export function cameBackCancelled(params: URLSearchParams): boolean {
  const status = (params.get("status") ?? "").toLowerCase();
  return ["cancelled", "canceled", "abandoned"].includes(status) || params.get("cancelled") === "true";
}

// The reference of the payment this tab sent the person to pay, kept for the return page when the
// provider's address brings nothing back. Per tab; a failure to read or write it is never fatal.
const PENDING_KEY = "beyric.billing.reference";
export function rememberReference(reference: string): void {
  try {
    window.sessionStorage.setItem(PENDING_KEY, reference);
  } catch {
    // the page works without it
  }
}
export function rememberedReference(): string | null {
  try {
    return window.sessionStorage.getItem(PENDING_KEY);
  } catch {
    return null;
  }
}
export function forgetReference(): void {
  try {
    window.sessionStorage.removeItem(PENDING_KEY);
  } catch {
    // nothing to forget
  }
}

// How long to wait between reads of GET /payments/{reference} (the guide's cadence): every 2 seconds
// for the first 30 seconds, then every 10 seconds until 5 minutes, then stop (null).
export const POLL_FAST_MS = 2_000;
export const POLL_SLOW_MS = 10_000;
export const POLL_FAST_UNTIL_MS = 30_000;
export const POLL_STOP_MS = 5 * 60_000;
export function nextPollDelay(elapsedMs: number): number | null {
  if (elapsedMs >= POLL_STOP_MS) return null;
  return elapsedMs < POLL_FAST_UNTIL_MS ? POLL_FAST_MS : POLL_SLOW_MS;
}

// ---- who sees what ----

// A 404 `not_found` from any billing route means billing is not available to this person (off, or not
// on the allow-list): the screens then show nothing at all, not an error.
export const BILLING_PLANS_HREF = "/billing/plans";
export const BILLING_HREF = "/billing";

// A person who holds a plan period in force (`active`, or `past_due` in its 3 days of grace) manages it on
// Billing; everyone else (no plan, a trial, a lapsed or ended plan) is sent to choose one.
export function hasLivePlan(state: Pick<BillingState, "subscription">): boolean {
  const status = state.subscription?.status;
  return status === "active" || status === "past_due";
}

// Where every "your hours / your plan" entry point of the app leads for this person.
export function billingEntryHref(state: Pick<BillingState, "subscription">): string {
  return hasLivePlan(state) ? BILLING_HREF : BILLING_PLANS_HREF;
}

// The three gated features answer 403 `plan_inactive`; it is not transient, so it is never retried and
// always leads to the plans screen.
// The words that tell the two refusals with a way forward apart (see components/billing/gate-text.tsx). Both are
// pinned to the sentences captured in docs/fe-captures/feature-gates and docs/fe-captures/hours-usage by
// web/tests/billing-gates.test.tsx.
export const GATE_PLAN_MARK = "needs an active plan or trial";
export const GATE_HOURS_MARK = "will not join new meetings";

export function isPlanInactive(error: unknown): boolean {
  return typeof error === "object" && error !== null && "code" in error && (error as { code: unknown }).code === "plan_inactive";
}

// ---- what the banner remembers for this browser tab ----
// A 404 means billing is not available to this person; a healthy answer (no banner to show) is good for a minute.
// Both are per tab (`sessionStorage`), and both are forgotten when this client signs someone in or out. A failure to
// read or write them is never fatal: the banner then simply asks.
const UNAVAILABLE_KEY = "beyric.billing.unavailable";
const HEALTHY_KEY = "beyric.billing.healthy";
export const HEALTHY_FOR_MS = 60_000;

export function billingKnownAbsent(): boolean {
  try {
    return window.sessionStorage.getItem(UNAVAILABLE_KEY) === "1";
  } catch {
    return false;
  }
}
export function rememberBillingAbsent(): void {
  try {
    window.sessionStorage.setItem(UNAVAILABLE_KEY, "1");
  } catch {
    // asking again next time is only a wasted request
  }
}
export function billingRecentlyHealthy(now: number = Date.now()): boolean {
  try {
    const at = Number(window.sessionStorage.getItem(HEALTHY_KEY));
    return at > 0 && now - at < HEALTHY_FOR_MS;
  } catch {
    return false;
  }
}
export function rememberBillingHealthy(now: number = Date.now()): void {
  try {
    window.sessionStorage.setItem(HEALTHY_KEY, String(now));
  } catch {
    // see above
  }
}
// Counts the times the tab forgot (a sign-in or sign-out here): an answer kept in memory for a few seconds is
// only good in the epoch it was read in.
let tabEpoch = 0;
export function billingTabEpoch(): number {
  return tabEpoch;
}
export function forgetBillingTabState(): void {
  tabEpoch += 1;
  try {
    window.sessionStorage.removeItem(UNAVAILABLE_KEY);
    window.sessionStorage.removeItem(HEALTHY_KEY);
  } catch {
    // nothing to forget
  }
}
