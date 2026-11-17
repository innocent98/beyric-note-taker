"use client";

import { useEffect, useState } from "react";
import { Alert, Badge, Button } from "@/components/ui";
import { LocalTime } from "@/components/app/local-time";
import { api } from "@/lib/api/browser";
import { BILLING_HREF, BILLING_PLANS_HREF, formatHours, type BillingState } from "@/lib/billing";
import { billingErrorMessage, billingUnavailable } from "./common";

// The "Plan and billing" card of Settings (billing stage S8b, LEAD-DESIGNED: the Settings card with a
// summary and the way into /billing). Nothing is drawn while billing is not available to this person
// (the routes answer 404), and nothing while the first read is on its way.
export function SettingsBilling() {
  const [state, setState] = useState<BillingState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api.billing
      .state()
      .then((answer) => alive && setState(answer))
      .catch((caught) => {
        if (alive && !billingUnavailable(caught)) setError(billingErrorMessage(caught));
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!state && !error) return null;
  const sub = state?.subscription ?? null;
  const left = state?.hours.seconds_left ?? null;
  const live = sub !== null && (sub.status === "active" || sub.status === "past_due");

  return (
    <section className="card bill-card" aria-labelledby="set-billing">
      <div>
        <h2 id="set-billing" className="set-title">
          Plan and billing
        </h2>
        <p className="set-sub">Your plan, your recorded hours and your payments.</p>
      </div>
      {error ? <Alert kind="error">{error}</Alert> : null}
      {state ? (
        <div className="bill-summary">
          {live && sub ? (
            <>
              <div className="pl-row-title">
                {sub.plan_name}{" "}
                <Badge tone={sub.status === "past_due" ? "danger" : sub.cancel_at_period_end ? "warning" : "success"} dot>
                  {sub.status === "past_due" ? "Payment overdue" : sub.cancel_at_period_end ? "Ending" : "Active"}
                </Badge>
              </div>
              <div className="pl-row-sub">
                {sub.cancel_at_period_end ? "Ends on " : sub.status === "past_due" ? "Pay before " : "Renews on "}
                <LocalTime iso={(sub.status === "past_due" && sub.grace_ends_at) || sub.current_period_end} kind="day" />
                {left !== null ? <> · {formatHours(left)} left</> : null}
              </div>
            </>
          ) : (
            <>
              <div className="pl-row-title">You do not have a plan</div>
              <div className="pl-row-sub">
                {left !== null && left > 0 ? `${formatHours(left)} left.` : "Choose a plan to get recorded hours every month."}
              </div>
            </>
          )}
        </div>
      ) : null}
      <div className="pl-controls">
        <Button href={BILLING_HREF} kind="secondary" size="sm">
          Open billing
        </Button>
        {state && !live ? (
          <Button href={BILLING_PLANS_HREF} kind="primary" size="sm">
            Choose a plan
          </Button>
        ) : null}
      </div>
    </section>
  );
}
