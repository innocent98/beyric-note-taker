"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/browser";
import { billingKnownAbsent, billingTabEpoch, rememberBillingAbsent, type BillingState } from "@/lib/billing";
import { billingUnavailable } from "./common";

// What the app's billing entry points (the sidebar's Billing link and hours card, the notification links) read:
//   null            not known yet, or billing is not available to this person: draw nothing
//   BillingState    billing is on for this person
// It is the same GET /api/billing/subscription the banner and the Settings card ask. A 404 `not_found` means
// billing is off for this person and is remembered for the tab (`billingKnownAbsent`), exactly like the banner.
// Several entry points on one page share one request, and the answer is reused for a few seconds so moving
// between pages does not ask again on every click; it is short on purpose, so a plan bought a moment ago shows.
const FRESH_MS = 10_000;
let inflight: Promise<BillingState | null> | null = null;
let cached: { at: number; epoch: number; state: BillingState | null } | null = null;

function read(): Promise<BillingState | null> {
  if (billingKnownAbsent()) return Promise.resolve(null);
  if (cached && cached.epoch === billingTabEpoch() && Date.now() - cached.at < FRESH_MS) return Promise.resolve(cached.state);
  if (!inflight) {
    const epoch = billingTabEpoch();
    inflight = Promise.resolve()
      .then(() => api.billing.state())
      .then((state) => {
        cached = { at: Date.now(), epoch, state };
        return state;
      })
      .catch((caught) => {
        if (billingUnavailable(caught)) rememberBillingAbsent();
        // Any other failure draws nothing this time and is not remembered: the next page asks again.
        return null;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

// For tests, and for the moment a person signs out: forget what was read.
export function forgetBillingEntry(): void {
  cached = null;
  inflight = null;
}

export function useBillingState(): BillingState | null {
  const [state, setState] = useState<BillingState | null>(null);
  useEffect(() => {
    let alive = true;
    void read().then((answer) => {
      if (alive) setState(answer);
    });
    return () => {
      alive = false;
    };
  }, []);
  return state;
}
