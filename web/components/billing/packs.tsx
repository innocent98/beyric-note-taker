"use client";

import { useCallback, useState } from "react";
import { Alert, AppLink } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { BILLING_PLANS_HREF, formatHours, formatMoney, rememberReference, type PackOffer, type Subscription } from "@/lib/billing";
import { goTo } from "@/lib/navigate";
import { billingErrorMessage } from "./common";
import { usePageRestore } from "./use-page-restore";

// Extra hours (billing stage S8b part B). A pack is valid only with a live plan (the server answers 409
// `plan_required` otherwise, and a plan in its grace may still buy one), and is paid on the route the
// plan was sold on, so the price shown is `GET /plans`'s own. It adds hours when the payment is verified.
export function Packs({ packs, sub }: { packs: PackOffer[]; sub: Subscription | null }) {
  const live = sub !== null && (sub.status === "active" || sub.status === "past_due");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  usePageRestore(useCallback(() => setBusy(null), []));

  async function buy(pack: PackOffer) {
    if (busy) return;
    setBusy(pack.code);
    setError(null);
    try {
      const started = await api.billing.checkout("pack", pack.code);
      rememberReference(started.payment.reference);
      goTo(started.checkout_url);
    } catch (caught) {
      setError(billingErrorMessage(caught));
      setBusy(null);
    }
  }

  return (
    <section className="card bill-card" aria-labelledby="bill-packs-title">
      <div>
        <h2 id="bill-packs-title" className="set-title">
          Extra hours
        </h2>
        <p className="set-sub">
          Add recorded hours on top of your plan. They are paid once, not every month, and need a plan to buy.
        </p>
      </div>
      {!live ? (
        <p className="set-sub">
          You need a plan to buy extra hours. <AppLink href={BILLING_PLANS_HREF}>Choose a plan</AppLink>
        </p>
      ) : packs.length === 0 ? (
        <p className="set-sub">No extra hours are on sale for your currency yet.</p>
      ) : (
        packs.map((pack) => (
          <div className="pl-row pl-row-center bill-option-row" key={pack.code}>
            <div className="pl-grow">
              <div className="pl-row-title">{pack.name}</div>
              <div className="pl-row-sub">
                {formatHours(pack.seconds)} · {formatMoney(pack.price.amount_minor, pack.price.currency)} once · valid for{" "}
                {pack.valid_months} months from the day they are added
              </div>
            </div>
            <div className="pl-controls">
              <button type="button" className="btn btn-secondary pl-btn" disabled={busy !== null} onClick={() => void buy(pack)}>
                {busy === pack.code ? "Opening the payment page..." : "Buy these hours"}
              </button>
            </div>
          </div>
        ))
      )}
      {error ? <Alert kind="error">{error}</Alert> : null}
    </section>
  );
}
