"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Alert, Button } from "@/components/ui";
import { LocalTime } from "@/components/app/local-time";
import { api } from "@/lib/api/browser";
import {
  BILLING_HREF,
  BILLING_PLANS_HREF,
  billingKnownAbsent,
  billingRecentlyHealthy,
  rememberBillingAbsent,
  rememberBillingHealthy,
  forgetBillingTabState,
  type BillingState,
} from "@/lib/billing";
import { billingUnavailable } from "./common";
import { PayNow } from "./pay-now";

// The app-wide notice for a plan whose payment failed (billing stage S8b part C), drawn by the shell on
// every screen of the owner app except the billing pages (which say it in full). It reads
// GET /api/billing/subscription:
//   past_due   the 3 days of grace: the date it ends, "no new hours until paid", and Pay now. A charge the
//              person's bank wants confirmed (3-D Secure) shows "Finish paying", which goes to THAT
//              charge's own page (`pending_payment.pay_url`), never to a second checkout.
//   lapsed     the grace ended unpaid: what is closed, what is kept, and the way back (choose a plan, or
//              confirm the charge still waiting: paying it late brings the plan back).
// A 404 means billing is not available to this person: nothing is drawn, and the tab remembers it so the
// question is not asked again on every page. A healthy answer (nothing to say) is remembered for a minute for the
// same reason; an answer that shows a notice is never remembered, so paying makes it go away at the next page. While a banner shows, the state is read again every 60
// seconds and when the window gains focus (the guide's polling). Nothing here ever says "paid".
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
        if (billingUnavailable(caught)) rememberBillingAbsent();
      });
  }, []);

  useEffect(() => {
    if (onBillingPage || billingKnownAbsent() || billingRecentlyHealthy()) return;
    let alive = true;
    api.billing
      .state()
      .then((answer) => {
        const shows = answer.subscription?.status === "past_due" || answer.subscription?.status === "lapsed";
        if (shows) forgetBillingTabState();
        else rememberBillingHealthy();
        if (alive) setState(answer);
      })
      .catch((caught) => {
        if (billingUnavailable(caught)) rememberBillingAbsent();
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
          <strong>Your plan has ended because a payment was not made.</strong> Ask Beyric, translating and rewriting notes
          are closed, and new recordings, including the meetings your note taker joins from your calendar, only start
          {hasHours ? " from the extra hours you still hold" : " once you have hours again"}. You can still read, search and
          export your notes.
          {state.pending_payment?.kind === "renewal" && state.pending_payment.pay_url
            ? " You can finish paying the charge that is still waiting, and your plan comes back, or choose a plan instead. Do one of the two, not both."
            : ""}
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
