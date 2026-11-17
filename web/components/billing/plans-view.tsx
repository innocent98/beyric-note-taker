"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, AppLink, Badge, Button, Card, EmptyState, PageHead } from "@/components/ui";
import { api } from "@/lib/api/browser";
import {
  BILLING_HREF,
  currencyWords,
  formatHours,
  formatMoney,
  samePrice,
  rememberReference,
  routeStatement,
  type BillingProfile,
  type BillingState,
  type PlanOffer,
  type Plans,
} from "@/lib/billing";
import { goTo } from "@/lib/navigate";
import { BillingCountry } from "./billing-country";
import { billingErrorMessage, billingUnavailable } from "./common";
import { usePageRestore } from "./use-page-restore";

type Loaded = { plans: Plans; state: BillingState; profile: BillingProfile };

// "Choose a plan" (billing stage S8b, part A). LEAD-DESIGNED from the existing components: no billing
// design exists. The page reads GET /plans (what can be bought, priced in the person's route), the
// state and the billing profile; the person picks a billing country first, reads the plain statement
// ("Prices in naira, paid through Paystack."), then a plan button starts the hosted checkout
// (POST /checkout) and the browser goes to the provider's page. Nothing is granted by pressing a button
// or by coming back: the return page waits for a verified payment (components/billing/return-view.tsx).
// No price is written here; a plan with no price in the person's currency says it is not on sale yet
// and is never hidden.
export function PlansView() {
  const [data, setData] = useState<Loaded | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The plan whose button was pressed while another plan payment is still waiting: it asks once more first.
  const [startOver, setStartOver] = useState<string | null>(null);

  usePageRestore(useCallback(() => setBusy(null), []));
  const [reads, setReads] = useState(0);
  const load = useCallback(() => {
    setLoadError(null);
    setReads((n) => n + 1);
  }, []);

  useEffect(() => {
    let alive = true;
    Promise.all([api.billing.plans(), api.billing.state(), api.billing.profile()])
      .then(([plans, state, profile]) => {
        if (!alive) return;
        setData({ plans, state, profile });
        setLoadError(null);
      })
      .catch((caught) => {
        if (!alive) return;
        if (billingUnavailable(caught)) setUnavailable(true);
        else setLoadError(billingErrorMessage(caught));
      });
    return () => {
      alive = false;
    };
  }, [reads]);

  async function choose(plan: PlanOffer, waitingPlan: boolean) {
    if (busy) return;
    // Two different plan pages can both be paid, and the second would have to be refunded by hand: ask first.
    if (waitingPlan && startOver !== plan.code) {
      setStartOver(plan.code);
      return;
    }
    setStartOver(null);
    setBusy(plan.code);
    setError(null);
    try {
      const started = await api.billing.checkout("plan", plan.code);
      if (plan.price && !samePrice(started.payment, plan.price)) {
        setError(
          `The price of this plan is now ${formatMoney(started.payment.amount_minor, started.payment.currency)}, not the ${formatMoney(plan.price.amount_minor, plan.price.currency)} shown. Nothing was charged. We have refreshed the page; check the price and choose again.`,
        );
        setBusy(null);
        load();
        return;
      }
      rememberReference(started.payment.reference);
      goTo(started.checkout_url);
    } catch (caught) {
      setError(billingErrorMessage(caught));
      setBusy(null);
    }
  }

  if (unavailable) {
    return (
      <div className="stack-md measure">
        <PageHead title="Choose a plan" />
        <Card>
          <EmptyState title="Plans are not available for your account yet">
            <p>We will tell you when you can choose one.</p>
          </EmptyState>
        </Card>
      </div>
    );
  }
  // A failed first read is the whole page; a failed re-read keeps what is on screen and says so above it.
  if (loadError && !data) {
    return (
      <div className="stack-md measure">
        <PageHead title="Choose a plan" />
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
        <PageHead title="Choose a plan" />
        <p className="set-sub" role="status">
          Loading plans...
        </p>
      </div>
    );
  }

  const { plans, state, profile } = data;
  const sub = state.subscription;
  const hasPlan = sub !== null && (sub.status === "active" || sub.status === "past_due");
  const route = plans.route;
  const waiting = state.pending_payment;

  return (
    <div className="stack-md measure">
      <PageHead title="Choose a plan" subtitle="Each plan gives you recorded hours every month, for you alone." />
      {loadError ? (
        <Alert kind="error">
          <p className="alert-text">{loadError} What you see may be out of date.</p>
          <div className="alert-actions">
            <Button kind="secondary" size="sm" type="button" onClick={() => load()}>
              Try again
            </Button>
          </div>
        </Alert>
      ) : null}

      {hasPlan ? (
        <Alert kind="info">
          You already have a plan. To change it or add extra hours, open <AppLink href={BILLING_HREF}>Billing</AppLink>.
        </Alert>
      ) : null}

      {sub && !hasPlan ? (
        <Alert kind="info">
          Your last plan has ended. Choose a plan to start again. You can still read, search and export your notes.
        </Alert>
      ) : null}

      {waiting && waiting.pay_url && (waiting.kind === "plan" || waiting.kind === "pack") ? (
        <Alert kind="notice">
          <p className="alert-text">
            You started a payment and have not finished it. No hours are added until it is paid.
            {waiting.kind === "plan" ? " Do not pay it and a second plan: you would be charged twice." : ""}
          </p>
          <div className="alert-actions">
            <Button href={waiting.pay_url} kind="secondary" size="sm">
              Continue to pay
            </Button>
            <Button href={`/billing/return?reference=${encodeURIComponent(waiting.reference)}`} kind="quiet">
              Check this payment
            </Button>
          </div>
        </Alert>
      ) : null}

      <BillingCountry profile={profile} onChange={() => load()} />

      {route ? (
        <p className="bill-statement" role="status">
          <strong>{routeStatement(route)}</strong> You pay on the next page, on the provider&apos;s own site.
        </p>
      ) : (
        <Alert kind="notice">Choose your billing country first. Prices are shown once we know what you pay in.</Alert>
      )}

      {error ? <Alert kind="error">{error}</Alert> : null}

      <div className="stack-sm" aria-label="Plans">
        {plans.plans.length === 0 && route ? (
          <Card>
            <EmptyState title="No plans are on sale yet" />
          </Card>
        ) : null}
        {plans.plans.map((plan) => (
          <section className="card bill-plan" key={plan.code} aria-labelledby={`plan-${plan.code}`}>
            <div className="bill-plan-main">
              <h2 id={`plan-${plan.code}`} className="set-title">
                {plan.name}
              </h2>
              <p className="set-sub">{formatHours(plan.monthly_seconds)} of recording every month, for you alone.</p>
              {plan.price ? (
                <p className="bill-price">
                  <strong>{formatMoney(plan.price.amount_minor, plan.price.currency)}</strong> a month
                </p>
              ) : route ? (
                <p className="bill-price">
                  <Badge tone="warning">Not on sale yet</Badge> This plan is not on sale in {currencyWords(route.currency)} yet.
                </p>
              ) : null}
            </div>
            <div className="pl-controls">
              <button
                type="button"
                className="btn btn-primary pl-btn"
                disabled={!route || !plan.price || hasPlan || busy !== null}
                onClick={() =>
                  void choose(
                    plan,
                    waiting?.kind === "plan" &&
                      waiting.status === "pending" &&
                      !(plan.price && samePrice(waiting, plan.price)),
                  )
                }
              >
                {busy === plan.code ? "Opening the payment page..." : "Choose this plan"}
              </button>
            </div>
            {startOver === plan.code ? (
              <div className="bill-more-row">
                <Alert kind="notice" role="alert">
                  <p className="alert-text">
                    You already have a plan payment waiting. If you pay both, you will be charged twice. Start a new payment
                    for this plan anyway?
                  </p>
                  <div className="alert-actions">
                    <button type="button" className="btn btn-primary pl-btn" onClick={() => void choose(plan, true)}>
                      Yes, start a new payment
                    </button>
                    <button type="button" className="btn btn-secondary pl-btn" onClick={() => setStartOver(null)}>
                      No, keep the waiting one
                    </button>
                  </div>
                </Alert>
              </div>
            ) : null}
          </section>
        ))}
      </div>

      <section className="card bill-card" aria-labelledby="plans-before-pay">
        <h2 id="plans-before-pay" className="set-title">
          Before you pay
        </h2>
        <ul className="bill-rules">
          <li>Your plan renews every month by charging the card you pay with now. You can cancel any time.</li>
          <li>If you cancel, you keep your plan and your hours until the end of the month you paid for. Cancelling does not refund the unused part of the month; payments are refunded only under our refund rules.</li>
          <li>Unused plan hours do not carry over to the next month.</li>
          <li>If our price changes, it applies from your next renewal.</li>
          <li>Your hours appear when the provider confirms your payment, not when you come back to this site.</li>
          <li>Extra hours can be added once you have a plan.</li>
        </ul>
      </section>
    </div>
  );
}
