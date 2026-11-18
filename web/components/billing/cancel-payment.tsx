"use client";

import { useState } from "react";
import { Button } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError } from "@/lib/api/client";
import { forgetReference, rememberedReference, type BillingPayment, type CancelPaymentResult } from "@/lib/billing";
import { billingErrorMessage } from "./common";

// What the page says once a cancel is answered. `paid` is good news the person did not expect: the page was paid
// after all (in another tab, or a moment ago), the server settled it and did NOT cancel it, so it says so.
export function cancelNotice(result: CancelPaymentResult["result"]): { kind: "info"; text: string } {
  if (result === "paid") {
    return {
      kind: "info",
      text: "That payment had already gone through, so it was not cancelled. Your plan and hours are shown below.",
    };
  }
  return {
    kind: "info",
    text: "The payment was cancelled and no hours were added. Please do not pay on the old payment page.",
  };
}

// "Cancel this payment" for a plan or pack checkout the person started and did not finish (`pending_payment`). The
// server asks the payment provider first and only closes a payment the provider says is unpaid; when the provider
// cannot be asked it changes nothing and says so here, and the button stays so the person can try again.
export function CancelPayment({
  payment,
  onDone,
}: {
  payment: Pick<BillingPayment, "reference" | "kind">;
  // `null` when the server no longer knows the payment (404): the page just reads again, with nothing to announce.
  onDone: (result: CancelPaymentResult["result"] | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (payment.kind !== "plan" && payment.kind !== "pack") return null;

  async function cancel() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const done = await api.billing.cancelPayment(payment.reference);
      if (rememberedReference() === payment.reference) forgetReference();
      setBusy(false);
      onDone(done.result);
    } catch (caught) {
      // A 404 here means the payment is no longer ours to cancel (billing was switched off for this person):
      // the page reads again and shows what is true now.
      if (caught instanceof ApiError && caught.status === 404) {
        setBusy(false);
        onDone(null);
        return;
      }
      setError(billingErrorMessage(caught));
      setBusy(false);
    }
  }

  return (
    <>
      <Button type="button" kind="quiet" disabled={busy} onClick={() => void cancel()}>
        {busy ? "Cancelling..." : "Cancel this payment"}
      </Button>
      {error ? (
        <span className="alert-text" role="alert">
          {error}
        </span>
      ) : null}
    </>
  );
}
