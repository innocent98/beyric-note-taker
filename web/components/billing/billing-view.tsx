"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Button, Card, EmptyState, PageHead } from "@/components/ui";
import { LocalTime } from "@/components/app/local-time";
import { api } from "@/lib/api/browser";
import {
  BILLING_PLANS_HREF,
  formatHours,
  formatMoney,
  type BillingPayment,
  type BillingProfile,
  type BillingState,
  type Plans,
  type Subscription,
} from "@/lib/billing";
import { BillingCountry } from "./billing-country";
import { ChangePlan } from "./change-plan";
import { billingErrorMessage, billingUnavailable } from "./common";
import { HoursCard } from "./hours-card";
import { Packs } from "./packs";
import { PayNow } from "./pay-now";
import { PaymentHistory } from "./payment-history";

type Loaded = { state: BillingState; plans: Plans; payments: BillingPayment[]; profile: BillingProfile };

// The billing page (billing stage S8b part B), opened from Settings. LEAD-DESIGNED from the Settings
// cards: no billing design exists. It shows the plan and its status, the hours, the renewal and what it
// costs, cancel and resume, change plan with the price before the person commits, extra hours, the billing
// country and the payments. The buttons come from the `can_*` flags of the subscription and are never
// worked out here. Every consequence the guide lists is written where the button is (no refund of the unused
// part on cancel; a price that changed at renewal; no new hours in the grace; an upgrade is charged at once).
export function BillingView() {
  const [data, setData] = useState<Loaded | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // `reads` counts the times the page asked again (after a change, on focus, "Try again"): the effect below
  // reads when it changes, and the data on screen stays until the new answer arrives.
  const [reads, setReads] = useState(0);
  const load = useCallback(() => {
    setLoadError(null);
    setReads((n) => n + 1);
  }, []);

  useEffect(() => {
    let alive = true;
    Promise.all([api.billing.state(), api.billing.plans(), api.billing.payments(), api.billing.profile()])
      .then(([state, plans, payments, profile]) => alive && setData({ state, plans, payments, profile }))
      .catch((caught) => {
        if (!alive) return;
        if (billingUnavailable(caught)) setUnavailable(true);
        else setLoadError(billingErrorMessage(caught));
      });
    return () => {
      alive = false;
    };
  }, [reads]);

  // The guide: read the state again when the page gains focus (a payment finished in another tab).
  useEffect(() => {
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [load]);

  if (unavailable) {
    return (
      <div className="stack-md measure">
        <PageHead title="Billing" />
        <Card>
          <EmptyState title="Billing is not available for your account yet">
            <p>We will tell you when it is.</p>
          </EmptyState>
        </Card>
      </div>
    );
  }
  if (loadError) {
    return (
      <div className="stack-md measure">
        <PageHead title="Billing" />
        <Alert kind="error">{loadError}</Alert>
        <div>
          <Button kind="secondary" type="button" onClick={() => load()}>
            Try again
          </Button>
        </div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="stack-md measure">
        <PageHead title="Billing" />
        <p className="set-sub" role="status">
          Loading billing...
        </p>
      </div>
    );
  }

  const { state, plans, payments, profile } = data;
  const sub = state.subscription;
  const live = sub !== null && (sub.status === "active" || sub.status === "past_due");

  return (
    <div className="stack-md measure">
      <PageHead title="Billing" subtitle="Your plan, your hours and your payments." />
      {state.pending_payment && state.pending_payment.kind !== "renewal" ? (
        <WaitingPayment payment={state.pending_payment} />
      ) : null}
      <PlanCard state={state} plans={plans} payments={payments} onChanged={() => load()} />
      <HoursCard state={state} />
      {live && sub?.can_change_plan ? <ChangePlan sub={sub} plans={plans} onChanged={() => load()} /> : null}
      {live && sub?.status === "past_due" ? (
        <Alert kind="info">A plan cannot be changed while a payment is overdue. Pay it first, or cancel.</Alert>
      ) : null}
      <Packs packs={plans.packs} sub={sub} />
      <BillingCountry profile={profile} onChange={() => load()} />
      <PaymentHistory payments={payments} />
    </div>
  );
}

function WaitingPayment({ payment }: { payment: BillingPayment }) {
  return (
    <Alert kind="notice">
      <p className="alert-text">
        You have a payment that is not finished: {formatMoney(payment.amount_minor, payment.currency)}. No hours are added until
        it is paid.
      </p>
      <div className="alert-actions">
        {payment.pay_url ? (
          <Button href={payment.pay_url} kind="secondary" size="sm">
            Continue to pay
          </Button>
        ) : null}
        <Button href={`/billing/return?reference=${encodeURIComponent(payment.reference)}`} kind="quiet">
          Check this payment
        </Button>
      </div>
    </Alert>
  );
}

// The latest price a person was charged for a plan month (the first payment or a renewal): what the next
// renewal is compared with, so a price that changed is said and not shown silently.
function lastPlanAmount(payments: BillingPayment[], currency: string): number | null {
  const paid = payments.find((p) => p.status === "succeeded" && (p.kind === "plan" || p.kind === "renewal") && p.currency === currency);
  return paid ? paid.amount_minor : null;
}

function PlanCard({
  state,
  plans,
  payments,
  onChanged,
}: {
  state: BillingState;
  plans: Plans;
  payments: BillingPayment[];
  onChanged: () => void;
}) {
  const sub = state.subscription;
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(call: () => Promise<Subscription>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await call();
      setConfirming(false);
      onChanged();
    } catch (caught) {
      setError(billingErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  if (!sub || sub.status === "lapsed" || sub.status === "canceled" || sub.status === "ended") {
    return (
      <section className="card bill-card" aria-labelledby="bill-plan-title">
        <h2 id="bill-plan-title" className="set-title">
          Your plan
        </h2>
        <p>
          <strong>You do not have a plan.</strong>
        </p>
        {sub?.status === "lapsed" ? (
          <p className="set-sub">
            Your plan ended on <LocalTime iso={sub.current_period_end} kind="day" /> because a payment was not made. If you pay
            the charge that is still waiting, your plan comes back.
          </p>
        ) : sub ? (
          <p className="set-sub">
            Your plan ended on <LocalTime iso={sub.current_period_end} kind="day" />.
          </p>
        ) : (
          <p className="set-sub">Choose a plan to get recorded hours every month, for you alone.</p>
        )}
        <p className="set-sub">Your notes, transcripts and exports stay as they are.</p>
        {state.pending_payment?.kind === "renewal" && state.pending_payment.pay_url ? <PayNow state={state} size="md" /> : null}
        <div>
          <Button href={BILLING_PLANS_HREF} kind="primary">
            Choose a plan
          </Button>
        </div>
      </section>
    );
  }

  const overdue = sub.status === "past_due";
  const lastPaid = lastPlanAmount(payments, sub.currency);
  const pendingPlan = sub.pending_plan_code ? plans.plans.find((p) => p.code === sub.pending_plan_code) : undefined;
  const planHours = plans.plans.find((p) => p.code === sub.plan_code)?.monthly_seconds;
  const tone = overdue ? "danger" : sub.cancel_at_period_end ? "warning" : "success";
  const label = overdue ? "Payment overdue" : sub.cancel_at_period_end ? "Ending" : "Active";

  return (
    <section className="card bill-card" aria-labelledby="bill-plan-title">
      <div className="bill-plan-head">
        <div>
          <h2 id="bill-plan-title" className="set-title">
            Your plan
          </h2>
          <p className="bill-plan-name">{sub.plan_name}</p>
          {planHours ? <p className="set-sub">{formatHours(planHours)} of recording every month</p> : null}
        </div>
        <Badge tone={tone} dot>
          {label}
        </Badge>
      </div>

      {overdue ? (
        <Alert kind="notice">
          <p className="alert-text">
            <strong>Your last payment did not go through.</strong> No new hours are added until you pay. The hours you have left
            from last month stay usable
            {sub.grace_ends_at ? (
              <>
                {" "}
                until <LocalTime iso={sub.grace_ends_at} kind="day" />
              </>
            ) : null}
            , and your plan ends then if it is still unpaid.
            {sub.next_retry_at && !sub.cancel_at_period_end ? (
              <>
                {" "}
                We will try your card again on <LocalTime iso={sub.next_retry_at} kind="day" />.
              </>
            ) : null}
          </p>
          <div className="alert-actions">
            <PayNow state={state} size="md" />
          </div>
        </Alert>
      ) : null}

      {!overdue && sub.cancel_at_period_end ? (
        <p>
          You cancelled. Your plan and your hours work until <LocalTime iso={sub.current_period_end} kind="day" />. You will not be
          charged again, and the part of this month you do not use is not refunded.
        </p>
      ) : null}

      {!overdue && !sub.cancel_at_period_end ? (
        <p>
          Your plan renews on <LocalTime iso={sub.current_period_end} kind="day" /> by charging the card you paid with. The next
          charge is <strong>{formatMoney(sub.amount_minor, sub.currency)}</strong>.
          {!sub.pending_plan_code && lastPaid !== null && lastPaid !== sub.amount_minor ? (
            <>
              {" "}
              That is not the {formatMoney(lastPaid, sub.currency)} you paid last time: the price changed.
            </>
          ) : null}
        </p>
      ) : null}

      {overdue && sub.cancel_at_period_end ? (
        <p className="set-sub">You cancelled, so we have stopped trying your card.</p>
      ) : null}

      {pendingPlan && !overdue ? (
        <p className="set-sub">
          On <LocalTime iso={sub.current_period_end} kind="day" /> you move to {pendingPlan.name}. Change that under Change plan.
        </p>
      ) : null}

      <div className="pl-controls bill-actions">
        {sub.can_resume ? (
          <button type="button" className="btn btn-primary pl-btn" disabled={busy} onClick={() => void run(api.billing.resume)}>
            {busy ? "Saving..." : "Keep my plan"}
          </button>
        ) : null}
        {sub.can_cancel && !confirming ? (
          <button type="button" className="btn btn-secondary pl-btn" disabled={busy} onClick={() => setConfirming(true)}>
            Cancel plan
          </button>
        ) : null}
      </div>

      {sub.can_cancel && confirming ? (
        <Alert kind="notice">
          <p className="alert-text">
            <strong>Cancel your plan?</strong> It keeps working until <LocalTime iso={sub.current_period_end} kind="day" />. You will
            not be charged again. The unused part of this month is not refunded, extra hours you bought stay valid, and you can
            take the cancel back until then.
            {overdue ? " Cancelling now also stops us trying your card." : ""}
          </p>
          <div className="alert-actions">
            <button type="button" className="btn btn-danger pl-btn" disabled={busy} onClick={() => void run(api.billing.cancel)}>
              {busy ? "Cancelling..." : "Yes, cancel my plan"}
            </button>
            <button type="button" className="btn btn-secondary pl-btn" disabled={busy} onClick={() => setConfirming(false)}>
              Keep my plan
            </button>
          </div>
        </Alert>
      ) : null}
      {error ? <Alert kind="error">{error}</Alert> : null}
    </section>
  );
}
