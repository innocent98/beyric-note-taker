import { AppLink } from "@/components/ui";
import { BILLING_HREF, BILLING_PLANS_HREF, GATE_HOURS_MARK, GATE_PLAN_MARK } from "@/lib/billing";

// The server's own sentence for a refusal that has a way forward, with that way written after it:
//  * 403 `plan_inactive` (Ask Beyric, a new translation, rewriting notes, sharing in French or Spanish):
//    "... needs an active plan or trial. ..." leads to the plans screen. Not transient, never retried.
//  * 403 `hours_cap_reached` (no hours, or none held): "... will not join new meetings. ..." leads to
//    Billing, where extra hours, an upgrade or a plan are. A `reserve` refusal is a wait, not a limit, and
//    has no such link.
// The sentence is shown as it comes (docs/fe-integration-guide-billing.md: "Show error.message as it is").
export function GateText({ message }: { message: string }) {
  if (message.includes(GATE_PLAN_MARK)) {
    return (
      <>
        {message} <AppLink href={BILLING_PLANS_HREF}>See plans</AppLink>
      </>
    );
  }
  if (message.includes(GATE_HOURS_MARK)) {
    return (
      <>
        {message} <AppLink href={BILLING_HREF}>Add hours or choose a plan</AppLink>
      </>
    );
  }
  return <>{message}</>;
}
