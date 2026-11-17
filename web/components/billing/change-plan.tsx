"use client";

import { useCallback, useState } from "react";
import { Alert, Badge } from "@/components/ui";
import { LocalTime } from "@/components/app/local-time";
import { api } from "@/lib/api/browser";
import {
  currencyWords,
  formatHours,
  formatMoney,
  rememberReference,
  type ChangePreview,
  type PlanOffer,
  type Plans,
  type Subscription,
} from "@/lib/billing";
import { goTo } from "@/lib/navigate";
import { billingErrorMessage } from "./common";
import { usePageRestore } from "./use-page-restore";

// Change plan (billing stage S8b part B). The server decides what a change is: asking for the preview
// first (GET /subscription/change-plan/preview) is what makes the price visible BEFORE the person commits,
// because an upgrade is charged the moment they confirm (guide, "What the person must be told", item 4).
//   upgrade    paid now for the days left of this month, through a checkout (the browser leaves for the provider)
//   downgrade  stored, applied at the next renewal at the smaller plan's price; nothing is charged now
//   clear      the plan they are on again: takes a waiting downgrade back
// Only shown while `can_change_plan` is true (never in the grace: the server would answer 409 `payment_overdue`).
export function ChangePlan({
  sub,
  plans,
  onChanged,
}: {
  sub: Subscription;
  plans: Plans;
  onChanged: () => void;
}) {
  const [preview, setPreview] = useState<{ plan: PlanOffer; answer: ChangePreview } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  usePageRestore(useCallback(() => setBusy(null), []));
  const others = plans.plans.filter((plan) => plan.code !== sub.plan_code);
  const waiting = sub.pending_plan_code ? plans.plans.find((plan) => plan.code === sub.pending_plan_code) : undefined;

  async function ask(plan: PlanOffer) {
    if (busy) return;
    setBusy(plan.code);
    setError(null);
    setPreview(null);
    try {
      setPreview({ plan, answer: await api.billing.previewChange(plan.code) });
    } catch (caught) {
      setError(billingErrorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function confirm(plan: PlanOffer) {
    if (busy) return;
    setBusy(plan.code);
    setError(null);
    try {
      const done = await api.billing.changePlan(plan.code);
      if (done.result === "checkout" && done.checkout_url && done.payment) {
        // The browser is leaving for the provider: the button stays busy so it cannot be pressed twice. It is
        // freed if the person comes back with the back button (`usePageRestore`).
        rememberReference(done.payment.reference);
        goTo(done.checkout_url);
        return;
      }
      setPreview(null);
      setBusy(null);
      onChanged();
    } catch (caught) {
      setError(billingErrorMessage(caught));
      setBusy(null);
    }
  }

  async function keepCurrent() {
    if (busy) return;
    setBusy(sub.plan_code);
    setError(null);
    try {
      await api.billing.changePlan(sub.plan_code);
      onChanged();
    } catch (caught) {
      setError(billingErrorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card bill-card" aria-labelledby="bill-change-title">
      <div>
        <h2 id="bill-change-title" className="set-title">
          Change plan
        </h2>
        <p className="set-sub">
          Moving to a bigger plan starts now and you pay the difference for the days left this month. Moving to a smaller
          plan starts at your next renewal; nothing is refunded and nothing changes before then.
        </p>
      </div>

      {sub.pending_plan_code && waiting ? (
        <Alert kind="info">
          <p className="alert-text">
            You move to <strong>{waiting.name}</strong> on <LocalTime iso={sub.current_period_end} kind="day" />, at{" "}
            {formatMoney(sub.amount_minor, sub.currency)} a month. Until then your plan stays as it is.
          </p>
          <div className="alert-actions">
            <button type="button" className="btn btn-secondary pl-btn" disabled={busy !== null} onClick={() => void keepCurrent()}>
              Keep {sub.plan_name}
            </button>
          </div>
        </Alert>
      ) : null}

      {others.length === 0 ? <p className="set-sub">There is no other plan to move to yet.</p> : null}
      {others.map((plan) => {
        const open = preview?.plan.code === plan.code ? preview.answer : null;
        const isWaiting = plan.code === sub.pending_plan_code;
        return (
          <div className="bill-option" key={plan.code}>
            <div className="pl-row pl-row-center bill-option-row">
              <div className="pl-grow">
                <div className="pl-row-title">
                  {plan.name} {isWaiting ? <Badge tone="info">Next month</Badge> : null}
                </div>
                <div className="pl-row-sub">
                  {formatHours(plan.monthly_seconds)} every month
                  {plan.price ? <> · {formatMoney(plan.price.amount_minor, plan.price.currency)} a month</> : null}
                </div>
              </div>
              <div className="pl-controls">
                {plan.price ? (
                  <button
                    type="button"
                    className="btn btn-secondary pl-btn"
                    disabled={busy !== null || isWaiting}
                    onClick={() => void ask(plan)}
                  >
                    {busy === plan.code && !open ? "Checking..." : "See what changes"}
                  </button>
                ) : (
                  <Badge tone="warning">Not on sale in {currencyWords(sub.currency)} yet</Badge>
                )}
              </div>
            </div>
            {open ? (
              <div className="bill-preview" role="status">
                {open.kind === "upgrade" && open.amount_minor !== null ? (
                  <>
                    <p className="alert-text">
                      <strong>You pay {formatMoney(open.amount_minor, open.currency)} today</strong> for the days left in
                      this month, and you get {open.extra_seconds ? `${formatHours(open.extra_seconds)} extra` : "extra hours"}{" "}
                      this month. From your next renewal the plan costs {plan.price ? formatMoney(plan.price.amount_minor, plan.price.currency) : "its full price"}{" "}
                      a month. You are charged as soon as you confirm, on the provider&apos;s page.
                    </p>
                    <button type="button" className="btn btn-primary pl-btn" disabled={busy !== null} onClick={() => void confirm(plan)}>
                      {busy === plan.code ? "Opening the payment page..." : `Confirm and pay ${formatMoney(open.amount_minor, open.currency)}`}
                    </button>
                  </>
                ) : null}
                {open.kind === "upgrade" && open.amount_minor === null ? (
                  <>
                    <p className="alert-text">
                      We could not work out the price of this change just now, so we have not offered it. Nothing was
                      charged.
                    </p>
                    <button type="button" className="btn btn-secondary pl-btn" disabled={busy !== null} onClick={() => void ask(plan)}>
                      Try again
                    </button>
                  </>
                ) : null}
                {open.kind === "downgrade" ? (
                  <>
                    <p className="alert-text">
                      <strong>Nothing is charged now.</strong> You keep {sub.plan_name} and your hours until{" "}
                      <LocalTime iso={sub.current_period_end} kind="day" />. Then you move to {plan.name} at{" "}
                      {open.amount_minor !== null ? formatMoney(open.amount_minor, open.currency) : "its price"} a month.
                    </p>
                    <button type="button" className="btn btn-primary pl-btn" disabled={busy !== null} onClick={() => void confirm(plan)}>
                      {busy === plan.code ? "Saving..." : `Move to ${plan.name} at my next renewal`}
                    </button>
                  </>
                ) : null}
              </div>
            ) : null}
          </div>
        );
      })}
      {error ? <Alert kind="error">{error}</Alert> : null}
    </section>
  );
}
