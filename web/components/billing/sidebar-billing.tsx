"use client";

import { AppLink, NavLink } from "@/components/ui";
import { BILLING_HREF, billingEntryHref, formatHours, hasLivePlan } from "@/lib/billing";
import { useBillingState } from "./use-billing-state";

// The signed-in app's two ways into billing, so nobody has to type /billing (billing entry points).
// Both draw NOTHING while billing is not available to the person (the same 404 the banner and the Settings
// card read) and while the first answer is on its way, so a person who is not on billing sees the sidebar
// exactly as it was.

// "Billing" under Configure, next to Settings. `current` is true on the three billing pages.
export function BillingNavLink({ current }: { current: boolean }) {
  const state = useBillingState();
  if (!state) return null;
  return <NavLink href={BILLING_HREF} label="Billing" current={current} />;
}

// The hours card above the person's name: what is left, as the bar the billing page draws, leading to Billing for
// someone with a plan and to the plans screen for everyone else (no plan, a trial, a pilot, a lapsed plan).
// A person with no limit on recorded hours (`seconds_left` null) has nothing to show and gets only the link.
export function SidebarHours() {
  const state = useBillingState();
  if (!state) return null;
  const { hours, subscription: sub } = state;
  const live = hasLivePlan(state);
  const left = hours.seconds_left;
  const held = hours.holdings_seconds;
  if (left === null || held === null) return null;

  const href = billingEntryHref(state);
  const out = hours.warning === "out" || left <= 0;
  const low = hours.warning === "low";
  const title = live && sub ? sub.plan_name : state.state === "trial" ? "Trial" : state.state === "pilot" ? "Pilot hours" : "No plan";
  const hoursLine = held === 0 ? "No recorded hours" : out ? "Out of hours" : low ? `${formatHours(left)} left, running low` : `${formatHours(left)} left`;
  const action = live ? (out ? "Add hours" : null) : "Choose a plan";

  return (
    <AppLink
      className={`side-hours${out ? " side-hours-out" : low ? " side-hours-low" : ""}`}
      href={href}
      data-side-hours={state.state}
      // The name is the visible words (label in name), with the destination when no action word is shown.
      aria-label={`${title}, ${hoursLine}. ${action ?? "Open billing"}`}
    >
      <span className="side-hours-title">{title}</span>
      <span className="side-hours-line">{hoursLine}</span>
      {held > 0 ? (
        <progress
          className="tk-ms-bar side-hours-bar"
          max={held}
          value={Math.min(Math.max(held - left, 0), held)}
          aria-hidden="true"
        />
      ) : null}
      {action ? <span className="side-hours-action">{action}</span> : null}
    </AppLink>
  );
}
