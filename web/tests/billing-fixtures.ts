import { readFileSync } from "node:fs";
import { join } from "node:path";
import { REPO } from "./helpers";

// A response body captured from the running app by scripts/capture_billing_subscriptions.py
// (docs/fe-captures/billing-subscriptions/<file>; the payment provider in those captures is a scripted double).
export function cap<T>(file: string): T {
  return JSON.parse(readFileSync(join(REPO, "docs/fe-captures/billing-subscriptions", file), "utf8")).response.body as T;
}

// One captured subscription state with a few fields changed.
export function stateWith<T extends object>(file: string, change: T = {} as T) {
  return { ...cap<Record<string, unknown>>(file), ...change } as ReturnType<typeof cap<Record<string, unknown>>> & T;
}

// A profile as the billing profile guide shows it (docs/fe-captures/billing-profile/).
export const PROFILE_US = {
  name_or_company: null,
  address: null,
  tax_id: null,
  billing_email: null,
  billing_country: "US",
  pending_billing_country: null,
  pending_effective_at: null,
  route: { currency: "USD", provider: "flutterwave" },
  country_change_allowed_at: null,
  country_locked: false,
  suggested_country: null,
  suggested_route: null,
};
export const PROFILE_NONE = {
  ...PROFILE_US,
  billing_country: null,
  route: null,
};

// A response body captured by scripts/capture_billing_invoices.py (docs/fe-captures/billing-invoices/<file>;
// the payment provider in those captures is a scripted double, and the seller's numbers are placeholders).
export function capInvoices<T>(file: string): T {
  return JSON.parse(readFileSync(join(REPO, "docs/fe-captures/billing-invoices", file), "utf8")).response.body as T;
}
