"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Alert, Button } from "@/components/ui";
import { LocalTime } from "@/components/app/local-time";
import { api } from "@/lib/api/browser";
import { BILLING_HREF, BILLING_PLANS_HREF, type BillingState } from "@/lib/billing";
import { billingUnavailable } from "./common";
import { PayNow } from "./pay-now";

// The app-wide notice for a plan whose payment failed (billing stage S8b part C), drawn by the shell on
// every screen of the owner app except the billing pages (which say it in full). It reads
// GET /api/billing/subscription:
//   past_due   the 3 days of grace: the date it ends, "no new hours until paid", and Pay now. A charge the
//              person's bank wants confirmed (3-D Secure) shows "Confirm with your bank", which goes to THAT
//              charge's own page (`pending_payment.pay_url`), never to a second checkout.
//   lapsed     the grace ended unpaid: what is closed, what is kept, and the way back (choose a plan, or
//              confirm the charge still waiting: paying it late brings the plan back).
// A 404 means billing is not available to this person: nothing is drawn, and the tab remembers it so the
// question is not asked again on every page. While a banner shows, the state is read again every 60
// seconds and when the window gains focus (the guide's polling). Nothing here ever says "paid".
const UNAVAILABLE_KEY = "beyric.billing.unavailable";

function remembered(): boolean {
  try {
    return window.sessionStorage.getItem(UNAVAILABLE_KEY) === "1";
  } catch {
    return false;
  }
}
function remember() {
  try {
    window.sessionStorage.setItem(UNAVAILABLE_KEY, "1");
  } catch {
    // asking again next time is only a wasted request
  }
}

export function BillingBanner() {
  const pathname = usePathname();
  const onBillingPage = pathname === "/billing" || (pathname ?? "").startsWith("/billing/");
  const [state, setState] = useState<BillingState | null>(null);

  // Any failure but "not available" leaves what is shown as it is: a banner must never turn into an
  // error on every page.
  const read = useCallback(() => {
    return api.billing
      .state()
      .then(setState)
      .catch((caught) => {
        if (billingUnavailable(caught)) remember();
      });
  }, []);

  useEffect(() => {
    if (onBillingPage || remembered()) return;
    let alive = true;
    api.billing
      .state()
      .then((answer) => alive && setState(answer))
      .catch((caught) => {
        if (billingUnavailable(caught)) remember();
      });
    return () => {
      alive = false;
    };
  }, [onBillingPage]);

  const status = state?.subscription?.status;
  const showing = !onBillingPage && (status === "past_due" || status === "lapsed");

  useEffect(() => {
    if (!showing) return;
    const timer = setInterval(() => void read(), 60_000);
    const onFocus = () => void read();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [showing, read]);

  if (!showing || !state || !state.subscription) return null;
  const sub = state.subscription;

  if (status === "past_due") {
    return (
      <div className="billing-banner" data-billing-banner="past_due">
        <Alert kind="notice" role="alert">
          <p className="alert-text">
            <strong>Your plan payment did not go through.</strong> No new hours are added until it is paid.
            {sub.grace_ends_at ? (
              <>
                {" "}
                Your plan ends on <LocalTime iso={sub.grace_ends_at} kind="day" /> if it stays unpaid.
              </>
            ) : null}{" "}
            You can still record from the hours you have left.
          </p>
          <div className="alert-actions">
            <PayNow state={state} />
            <Button href={BILLING_HREF} kind="quiet">
              Open billing
            </Button>
          </div>
        </Alert>
      </div>
    );
  }

  const hasHours = (state.hours.seconds_left ?? 0) > 0;
  return (
    <div className="billing-banner" data-billing-banner="lapsed">
      <Alert kind="notice" role="alert">
        <p className="alert-text">
          <strong>Your plan has ended because a payment was not made.</strong> New recordings, Ask Beyric, translating and
          rewriting notes are closed{hasHours ? ", except recordings from extra hours you still hold" : ""}. Your notes,
          transcripts and exports are all still here.
        </p>
        <div className="alert-actions">
          <PayNow state={state} />
          <Button href={BILLING_PLANS_HREF} kind={state.pending_payment?.pay_url ? "quiet" : "primary"} size="sm">
            Choose a plan
          </Button>
        </div>
      </Alert>
    </div>
  );
}
