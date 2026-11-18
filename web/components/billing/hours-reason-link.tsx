"use client";

import { AppLink } from "@/components/ui";
import { billingEntryHref, hasLivePlan } from "@/lib/billing";
import { useBillingState } from "./use-billing-state";

// The calendar's own "why it was not recorded" sentences (`NOT_RECORDED_REASON_TEXT`, app/calendar_text.py) for a
// meeting skipped because of hours. Pinned to the real sentences by web/tests/billing-entry.test.tsx.
export const HOURS_REASON_MARKS = ["recording hours are used up", "no recording hours"] as const;

export function isHoursReason(text: string | null | undefined): boolean {
  return !!text && HOURS_REASON_MARKS.some((mark) => text.includes(mark));
}

// After a "Not recorded" reason that is about hours: the way to get hours. Nothing while billing is not available
// to this person, and nothing for any other reason.
export function HoursReasonLink({ reason }: { reason: string | null | undefined }) {
  const state = useBillingState();
  if (!state || !isHoursReason(reason)) return null;
  return (
    <>
      {" "}
      <AppLink href={billingEntryHref(state)}>{hasLivePlan(state) ? "Add hours" : "Choose a plan"}</AppLink>
    </>
  );
}
