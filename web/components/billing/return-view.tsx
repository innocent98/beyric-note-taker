"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { useSearchParams } from "next/navigation";
import { Alert, Button, Card, PageHead } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError } from "@/lib/api/client";
import {
  cameBackCancelled,
  forgetReference,
  formatMoney,
  nextPollDelay,
  referenceFrom,
  rememberedReference,
  type BillingPayment,
} from "@/lib/billing";
import { billingErrorMessage, billingUnavailable } from "./common";

// Where the provider sends the person after the hosted page (`/billing/return`, billing stage S8b part A).
// The address only tells us WHICH payment to look at (our reference: `reference` or `trxref` from Paystack,
// `tx_ref` from Flutterwave); it never says "paid". The page reads the payment from us
// (GET /api/billing/payments/{reference}), every 2 seconds for 30 seconds, then every 10 seconds up to
// 5 minutes, and stops on any state but `pending`. "Paid" is shown only for `succeeded`.
//
// The states, in the person's words (the API has only pending, succeeded, failed, refunded and
// partly_refunded; "abandoned" and "mismatch" are NOT states of the payment, see below):
//   checking          pending, inside the 5 minutes
//   succeeded         paid and verified: the hours are in
//   failed            the provider says it did not go through; no hours
//   abandoned         the provider's address said the payer backed out, and the payment is still pending: no hours
//                     (the page keeps reading, a late payment still shows)
//   still confirming  pending after the 5 minutes. A payment the provider reports with the wrong amount, currency
//                     or reference is refused by the server and stays `pending` (guide, "What a wrong or short
//                     payment does"), so a "mismatch" can only look like this: the page says what to do (keep the
//                     reference). A distinct state would need an API change (reported to the lead).
//   refunded          the payment was refunded
//   unknown           no such payment for this person (404)
//   none              the address named no payment
type View =
  | { kind: "checking" }
  | { kind: "none" }
  | { kind: "unknown" }
  | { kind: "unavailable" }
  | { kind: "payment"; payment: BillingPayment; waited: boolean; hint: "cancelled" | null };

const subscribe = () => () => {};

export function ReturnView() {
  const params = useSearchParams();
  const fromAddress = referenceFrom(params);
  const cancelledHint = cameBackCancelled(params);
  // The address wins; a tab that sent the person to pay remembers the reference as a fallback (sessionStorage is
  // read in the browser only, so the first render, like the server's, has none).
  const inBrowser = useSyncExternalStore(subscribe, () => true, () => false);
  // Read once: it is forgotten as soon as the payment is settled, and the page must not then lose its payment.
  const [kept] = useState(rememberedReference);
  const reference = fromAddress ?? kept;
  const [view, setView] = useState<View>({ kind: "checking" });
  const [trouble, setTrouble] = useState<string | null>(null);
  // Bumped by "Check again": the effect below then starts its 5 minutes over.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!reference) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const startedAt = Date.now();

    async function read() {
      let delay: number | null;
      try {
        const payment = await api.billing.payment(reference as string);
        if (!alive) return;
        setTrouble(null);
        const pending = payment.status === "pending";
        delay = pending ? nextPollDelay(Date.now() - startedAt) : null;
        setView({ kind: "payment", payment, waited: pending && delay === null, hint: cancelledHint && pending ? "cancelled" : null });
        if (!pending) forgetReference();
      } catch (caught) {
        if (!alive) return;
        if (billingUnavailable(caught)) {
          setView({ kind: "unavailable" });
          return;
        }
        if (caught instanceof ApiError && caught.status === 404) {
          setView({ kind: "unknown" });
          return;
        }
        // A lost connection or a slow gateway is not an answer about the payment: say so and keep trying.
        setTrouble(billingErrorMessage(caught));
        delay = nextPollDelay(Date.now() - startedAt);
      }
      if (delay !== null) timer = setTimeout(() => void read(), delay);
    }

    void read();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [reference, cancelledHint, attempt]);

  const shown: View = !reference && inBrowser ? { kind: "none" } : view;
  return (
    <div className="stack-md measure">
      <PageHead title="Your payment" />
      <Body
        view={shown}
        trouble={trouble}
        onCheckAgain={() => {
          setView({ kind: "checking" });
          setAttempt((n) => n + 1);
        }}
      />
    </div>
  );
}

function Body({
  view,
  trouble,
  onCheckAgain,
}: {
  view: View;
  trouble: string | null;
  onCheckAgain: () => void;
}) {
  if (view.kind === "checking") return <Checking trouble={trouble} />;
  if (view.kind === "none") {
    return (
      <Card>
        <div className="stack-sm">
          <strong>There is no payment to check here.</strong>
          <p className="set-sub">Open Billing to see your payments.</p>
          <div>
            <Button href="/billing" kind="secondary">
              Go to Billing
            </Button>
          </div>
        </div>
      </Card>
    );
  }
  if (view.kind === "unknown") {
    return (
      <Card>
        <div className="stack-sm" role="alert">
          <strong>We could not find that payment.</strong>
          <p className="set-sub">It may belong to another account. Open Billing to see your own payments.</p>
          <div>
            <Button href="/billing" kind="secondary">
              Go to Billing
            </Button>
          </div>
        </div>
      </Card>
    );
  }
  if (view.kind === "unavailable") {
    return (
      <Card>
        <div className="stack-sm">
          <strong>Billing is not available for your account.</strong>
        </div>
      </Card>
    );
  }

  const { payment } = view;
  const what = FOR_WORDS[payment.kind];
  const amount = formatMoney(payment.amount_minor, payment.currency);
  const ref = (
    <p className="set-sub">
      Payment reference: <code>{payment.reference}</code>. Keep it if you need to ask us about this payment.
    </p>
  );

  if (payment.status === "succeeded") {
    return (
      <Card>
        <div className="stack-sm" role="status">
          <strong>Payment received.</strong>
          <p className="set-sub">{succeededWords(payment)}</p>
          <p className="set-sub">
            {amount} for {what}.
          </p>
          {ref}
          <div className="pl-controls bill-actions">
            <Button href="/billing" kind="primary">
              Go to Billing
            </Button>
            <Button href="/" kind="secondary">
              Back to meetings
            </Button>
          </div>
        </div>
      </Card>
    );
  }
  if (payment.status === "failed") {
    return (
      <Card>
        <div className="stack-sm" role="alert">
          <strong>The payment did not go through.</strong>
          <p className="set-sub">No hours were added and your plan has not changed. You can try again with the same card or another one.</p>
          {ref}
          <div className="pl-controls bill-actions">
            <Button href="/billing/plans" kind="primary">
              Try again
            </Button>
            <Button href="/billing" kind="secondary">
              Go to Billing
            </Button>
          </div>
        </div>
      </Card>
    );
  }
  if (payment.status === "refunded" || payment.status === "partly_refunded") {
    return (
      <Card>
        <div className="stack-sm" role="status">
          <strong>{payment.status === "refunded" ? "This payment was refunded." : "Part of this payment was refunded."}</strong>
          {ref}
          <div>
            <Button href="/billing" kind="secondary">
              Go to Billing
            </Button>
          </div>
        </div>
      </Card>
    );
  }

  // Pending.
  if (view.hint === "cancelled") {
    return (
      <Card>
        <div className="stack-sm" role="status">
          <strong>You did not finish paying.</strong>
          <p className="set-sub">
            No hours were added. If you did pay, keep this page open: it updates when the payment is confirmed.
          </p>
          {ref}
          <div className="pl-controls bill-actions">
            {payment.pay_url ? (
              <Button href={payment.pay_url} kind="primary">
                Go back to the payment page
              </Button>
            ) : null}
            <Button href="/billing" kind="secondary">
              Go to Billing
            </Button>
          </div>
        </div>
      </Card>
    );
  }
  if (view.waited) {
    return (
      <Card>
        <div className="stack-sm" role="status">
          <strong>We are still confirming your payment.</strong>
          <p className="set-sub">
            Your hours will appear when the payment is confirmed, and you can leave this page. If you paid and nothing
            changes within a few minutes, keep the reference below and tell us.
          </p>
          {ref}
          <div className="pl-controls bill-actions">
            <Button type="button" kind="secondary" onClick={onCheckAgain}>
              Check again
            </Button>
            {payment.pay_url ? (
              <Button href={payment.pay_url} kind="quiet">
                Go back to the payment page
              </Button>
            ) : null}
            <Button href="/billing" kind="quiet">
              Go to Billing
            </Button>
          </div>
        </div>
      </Card>
    );
  }
  return <Checking trouble={trouble} />;
}

function Checking({ trouble }: { trouble: string | null }) {
  return (
    <Card>
      <div role="status" className="stack-sm">
        <strong>Confirming your payment...</strong>
        <p className="set-sub">
          Please keep this page open. It usually takes a few seconds. Your hours are added when the payment is confirmed,
          not before.
        </p>
        {trouble ? <Alert kind="notice">{trouble} We will try again.</Alert> : null}
      </div>
    </Card>
  );
}

const FOR_WORDS: Record<BillingPayment["kind"], string> = {
  plan: "your plan",
  renewal: "your monthly renewal",
  upgrade: "your plan upgrade",
  pack: "extra hours",
};

function succeededWords(payment: BillingPayment): string {
  switch (payment.kind) {
    case "plan":
      return "Your plan is active and your hours are added.";
    case "upgrade":
      return "Your plan is upgraded and the extra hours are added.";
    case "renewal":
      return "Your plan is renewed and your hours are added.";
    case "pack":
      return "The extra hours are added.";
  }
}
