"use client";

import { useCallback, useState } from "react";
import { Alert, Button } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { rememberReference, type BillingState } from "@/lib/billing";
import { goTo } from "@/lib/navigate";
import { billingErrorMessage } from "./common";
import { usePageRestore } from "./use-page-restore";

// The one way to pay what is overdue, used by the banner and the billing page.
//  * A saved-card charge the bank wants the payer to confirm (3-D Secure) is already a payment of its own,
//    with its own page in `pending_payment.pay_url`: "Confirm with your bank" goes THERE. It must never be
//    answered by asking for a second checkout (docs/fe-integration-guide-billing.md, "A card that needs the
//    bank's confirmation"; the server's pay-link route would return the same address, but the screen does
//    not need to ask).
//  * Otherwise `POST /subscription/pay-link` makes (or returns) the provider's page for the overdue amount.
export function PayNow({ state, size = "sm" }: { state: BillingState; size?: "sm" | "md" }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  usePageRestore(useCallback(() => setBusy(false), []));
  const waiting = state.pending_payment;
  const bankPage = waiting && waiting.kind === "renewal" && waiting.status === "pending" ? waiting.pay_url : null;

  if (bankPage) {
    return (
      <Button href={bankPage} kind="primary" size={size}>
        Confirm with your bank
      </Button>
    );
  }
  if (!state.subscription?.can_pay_now) return null;

  async function pay() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const link = await api.billing.payLink();
      rememberReference(link.payment.reference);
      goTo(link.checkout_url);
    } catch (caught) {
      setError(billingErrorMessage(caught));
      setBusy(false);
    }
  }

  return (
    <>
      <Button type="button" kind="primary" size={size} disabled={busy} onClick={() => void pay()}>
        {busy ? "Opening the payment page..." : "Pay now"}
      </Button>
      {error ? <Alert kind="error">{error}</Alert> : null}
    </>
  );
}
