import { Alert, AppLink } from "@/components/ui";
import { LocalTime } from "@/components/app/local-time";
import { BILLING_PLANS_HREF, formatHours, type BillingState } from "@/lib/billing";

// The hours a person holds, used and left (billing stage S8b part B). The numbers are `hours` of
// GET /subscription: `seconds_left` is what they may still record, `holdings_seconds` what they hold in
// all. The words follow `hours.warning` and never the bar's ratio (docs/fe-integration-guide-billing.md,
// "UX consequences to surface"): `low` at 80% used, `out` when nothing is left. LEAD-DESIGNED: the
// progress bar is the one the Tasks milestones already draw.
export function HoursCard({ state }: { state: BillingState }) {
  const { hours, subscription: sub } = state;
  const inGrace = sub?.status === "past_due";
  const planEnded = state.state === "lapsed" || state.state === "none";
  const hasPlan = sub?.status === "active" || sub?.status === "past_due";
  const left = hours.seconds_left;
  const held = hours.holdings_seconds;

  return (
    <section className="card bill-card" aria-labelledby="bill-hours-title">
      <h2 id="bill-hours-title" className="set-title">
        Your hours
      </h2>

      {left === null || held === null ? (
        <p className="set-sub">There is no limit on recorded hours for your account.</p>
      ) : held === 0 ? (
        <p className="set-sub">You do not have any recorded hours at the moment.</p>
      ) : (
        <div className="bill-hours">
          <div className="bill-hours-line">
            <strong>{formatHours(left)} left</strong>
            <span className="pl-row-sub">
              {formatHours(held - left)} used of {formatHours(held)}
            </span>
          </div>
          <progress
            className="tk-ms-bar"
            max={held}
            value={Math.min(Math.max(held - left, 0), held)}
            aria-label={`${formatHours(held - left)} used of ${formatHours(held)}`}
          />
        </div>
      )}

      {hours.warning === "low" ? (
        <Alert kind="notice">
          You have used most of your hours. {planEnded ? "" : "Add extra hours or upgrade to keep recording."}
        </Alert>
      ) : null}
      {hours.warning === "out" ? (
        <Alert kind="notice">
          You have used all of your hours, so new recordings will not start. A meeting that is already running finishes
          and is counted in full.
          {planEnded
            ? ""
            : " They start again when you add extra hours, upgrade, or your plan renews. Ask Beyric, translating and rewriting notes keep working."}{" "}
         
        </Alert>
      ) : null}

      {inGrace && sub?.grace_ends_at ? (
        <p className="set-sub">
          These are the hours left from your last month. No new hours are added until your payment goes through, and they
          end on <LocalTime iso={sub.grace_ends_at} kind="day" /> if it stays unpaid.
        </p>
      ) : null}

      {planEnded ? (
        <p className="set-sub">
          Without a plan, Ask Beyric, translating and rewriting notes are closed. New recordings, including meetings your note
          taker would join from your calendar, only start from extra hours you still hold. You can still read, search and
          export your notes. <AppLink href={BILLING_PLANS_HREF}>Choose a plan</AppLink>
        </p>
      ) : hasPlan ? (
        <p className="pl-row-sub">
          Unused plan hours do not carry over to the next month. Extra hours stay valid for the time they were sold for.
        </p>
      ) : null}
    </section>
  );
}
