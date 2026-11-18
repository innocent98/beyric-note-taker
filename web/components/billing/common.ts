import { ApiError, errorMessage } from "@/lib/api/client";
import { currencyWords } from "@/lib/billing";

// Every refusal the billing routes can make is written for the person (docs/fe-integration-guide-billing.md,
// "Errors the screens must handle"), so the server's sentence is shown as it comes, with the currency code
// said in words. Everything else (a 404 on a payment, a gateway failure, a lost network) uses the shared words.
const SHOWABLE = new Set([
  "billing_country_missing",
  "billing_country_invalid",
  "billing_country_unsupported",
  "billing_email_invalid",
  "country_change_too_soon",
  "country_change_pending",
  "price_not_available",
  "plan_not_found",
  "product_not_found",
  "plan_required",
  "already_subscribed",
  "same_plan",
  "nothing_due",
  "not_canceled",
  "payment_overdue",
  "payment_in_progress",
  "no_subscription",
  "not_an_upgrade",
  "period_over",
  "period_almost_over",
  "provider_error",
  "provider_unavailable",
  "invoice_busy",
  "refund_pending",
  "invoice_failed",
]);

export function billingErrorMessage(error: unknown): string {
  if (error instanceof ApiError && SHOWABLE.has(error.code)) {
    return error.message.replace(/\b(NGN|USD)\b/g, (code) => currencyWords(code));
  }
  if (error instanceof ApiError && error.status === 404 && error.code === "not_found") {
    return "Billing is not available for your account.";
  }
  return errorMessage(error);
}

// A 404 `not_found` from a billing route: billing is off for this person. The screens then say nothing
// is available, or show nothing at all, and never an error.
export function billingUnavailable(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404 && error.code === "not_found";
}
