"use client";

import { useEffect, useMemo, useState } from "react";
import { Alert, SelectField, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { LocalTime } from "@/components/app/local-time";
import { api } from "@/lib/api/browser";
import { routeStatement, type BillingProfile, type BillingRoute } from "@/lib/billing";
import { countryName, countryOptions } from "@/lib/countries";
import { billingErrorMessage } from "./common";

// The "Billing country" card (LEAD-DESIGNED, no billing design exists: it reuses the Settings card, the
// standard select and buttons). The country decides what a person pays in and through whom, so it is
// CHOSEN from a list (never guessed from the address, the browser or the card), and the result is stated
// in words before any payment: the statement comes from GET /api/billing/route, read as the select
// changes, and never from a table in this file.
//
// The rules a person must know are said up front (docs/fe-integration-guide-billing-profile.md, "UX
// consequences"): the country is where they or their company is established; the first choice can be
// fixed once straight away, after that one change in 30 days; a change that crosses between Nigeria and
// elsewhere while a plan is live waits for the end of the paid month and cannot be undone while it waits.
export function BillingCountry({
  profile,
  onChange,
}: {
  profile: BillingProfile;
  onChange: (profile: BillingProfile) => void;
}) {
  const options = useMemo(() => countryOptions(), []);
  const chosen = profile.billing_country;
  const [editing, setEditing] = useState(chosen === null);
  const [picked, setPicked] = useState<string>("");
  // What the picked country would pay with, kept with the country it was read for, so a stale answer is never shown.
  const [read, setRead] = useState<{ country: string; route: BillingRoute | null; error: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, flash] = useToast();
  // "Now", read once when the card is drawn, to tell whether the next change is still too soon.
  const [now] = useState(() => Date.now());

  // Read what the picked country would pay with, so the person knows before they save.
  useEffect(() => {
    if (!picked) return;
    let alive = true;
    api.billing
      .routeFor(picked)
      .then((route) => alive && setRead({ country: picked, route: { currency: route.currency, provider: route.provider }, error: null }))
      .catch((caught) => alive && setRead({ country: picked, route: null, error: billingErrorMessage(caught) }));
    return () => {
      alive = false;
    };
  }, [picked]);
  const preview = picked && read?.country === picked ? read.route : null;
  const previewError = picked && read?.country === picked ? read.error : null;

  const waitingFor = profile.pending_billing_country;
  const allowedAt = profile.country_change_allowed_at;
  const tooSoon = allowedAt !== null && new Date(allowedAt).getTime() > now;

  async function save() {
    if (!picked || busy) return;
    setBusy(true);
    setError(null);
    try {
      const answer = await api.billing.setCountry(picked);
      onChange(answer.profile);
      setEditing(false);
      setPicked("");
      flash(
        answer.result === "scheduled"
          ? "Your change will take effect when your paid month ends"
          : answer.result === "unchanged"
            ? "That is already your billing country"
            : "Billing country saved",
      );
    } catch (caught) {
      setError(billingErrorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card bill-card" aria-labelledby="bill-country-title">
      <div>
        <h2 id="bill-country-title" className="set-title">
          Billing country
        </h2>
        <p className="set-sub">
          Choose the country you, or your company, are established in. It is not where you are right now, and it
          is not guessed. It decides whether you pay in naira or in US dollars.
        </p>
      </div>

      {chosen && !editing ? (
        <div className="bill-current">
          <div className="pl-row-title">{countryName(chosen)}</div>
          {profile.route ? <div className="pl-row-sub">{routeStatement(profile.route)}</div> : null}
        </div>
      ) : null}

      {waitingFor && profile.pending_effective_at ? (
        <Alert kind="info">
          Your billing country changes to {countryName(waitingFor)} on <LocalTime iso={profile.pending_effective_at} kind="day" />,
          when your paid month ends. Until then you keep paying as you do now. A waiting change cannot be undone.
        </Alert>
      ) : null}

      {editing ? (
        <div className="bill-form">
          <SelectField
            id="bill-country"
            label="Billing country"
            value={picked}
            onChange={(event) => setPicked(event.target.value)}
            options={[["", "Choose a country"], ...options]}
          />
          <div aria-live="polite" className="bill-statement">
            {picked && preview ? <strong>{routeStatement(preview)}</strong> : null}
            {previewError ? <Alert kind="error">{previewError}</Alert> : null}
          </div>
          <div className="pl-controls">
            <button type="button" className="btn btn-primary pl-btn" disabled={!picked || busy} onClick={() => void save()}>
              {busy ? "Saving..." : "Save country"}
            </button>
            {chosen ? (
              <button
                type="button"
                className="btn btn-secondary pl-btn"
                disabled={busy}
                onClick={() => {
                  setEditing(false);
                  setPicked("");
                  setError(null);
                }}
              >
                Cancel
              </button>
            ) : null}
          </div>
          {error ? <Alert kind="error">{error}</Alert> : null}
        </div>
      ) : null}

      {chosen && !editing && !waitingFor ? (
        <div className="pl-controls">
          <button type="button" className="btn btn-secondary pl-btn" disabled={tooSoon} onClick={() => setEditing(true)}>
            Change country
          </button>
          {tooSoon && allowedAt ? (
            <span className="pl-row-sub">
              You can change it again on <LocalTime iso={allowedAt} kind="day" />.
            </span>
          ) : null}
        </div>
      ) : null}

      <ul className="bill-rules">
        <li>You can fix a wrong first choice once, straight away. After that you can change it once every 30 days.</li>
        <li>
          If you already have a plan and you move between Nigeria and another country, the change waits until your paid
          month ends. You then pay again on the new route, because a saved card cannot be moved.
        </li>
        <li>
          A card from any country is accepted. If you are billed in US dollars, use a card your bank lets you spend in
          dollars. If you are established in Nigeria, choose Nigeria so you pay in naira.
        </li>
      </ul>
      <Toast message={toast} />
    </section>
  );
}
