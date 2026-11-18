"use client";

import { useEffect, useMemo, useState } from "react";
import { Alert, Field, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { LocalTime } from "@/components/app/local-time";
import { api } from "@/lib/api/browser";
import { routeStatement, type BillingProfile, type BillingRoute } from "@/lib/billing";
import { countryName, filterCountryOptions, regionFromLanguages } from "@/lib/countries";
import { billingErrorMessage } from "./common";

// The "Billing country" card (LEAD-DESIGNED, no billing design exists: it reuses the Settings card, the
// standard select and buttons). The country decides what a person pays in (Nigeria: naira; anywhere
// else: US dollars; the card does not name the payment provider), and the result is always stated in words,
// read from the server (GET /api/billing/route or the profile's route), never from a table in this file.
//
// The country is SUGGESTED, never asked cold and never saved by a guess (docs/fe-integration-guide-billing.md,
// "Billing country"): while none is saved, the card shows the country the saved time zone points to (else the
// region of the browser's language) as one line to confirm ("Use Nigeria") or change. Nothing is saved until
// the person confirms, either with that button or by going on to a plan's checkout (the plans screen saves the
// reported candidate first). With nothing to suggest, the picker shows Nigeria first, the common countries on
// top, and a search.
//
// Until the first payment the country changes instantly, as often as the person likes. From the first payment
// (profile.country_locked) it changes once every 30 days, and a change that crosses between Nigeria and
// elsewhere while a plan is live waits for the end of the paid month.
type Suggestion = { country: string; route: BillingRoute; from: string };

export function BillingCountry({
  profile,
  onChange,
  onCandidate,
}: {
  profile: BillingProfile;
  onChange: (profile: BillingProfile) => void;
  // The country the person is looking at but has not saved (the suggestion, or what the picker shows), or null.
  // The plans screen prices that country and saves it before it starts a checkout.
  onCandidate?: (country: string | null) => void;
}) {
  const chosen = profile.billing_country;
  const [editing, setEditing] = useState(false);
  // The person pressed "Not X? Change" on the suggestion.
  const [changing, setChanging] = useState(false);
  const [picked, setPicked] = useState<string>("");
  const [query, setQuery] = useState("");
  // What the picked country would pay with, kept with the country it was read for, so a stale answer is never shown.
  const [read, setRead] = useState<{ country: string; route: BillingRoute | null; error: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, flash] = useToast();
  // "Now", read once when the card is drawn, to tell whether the next change is still too soon.
  const [now] = useState(() => Date.now());

  // The suggestion: the server's (from the saved time zone), else the browser's language region.
  const { suggested_country: serverCountry, suggested_route: serverRoute } = profile;
  const fromServer = useMemo<Suggestion | null>(
    () => (serverCountry && serverRoute ? { country: serverCountry, route: serverRoute, from: "your time zone" } : null),
    [serverCountry, serverRoute],
  );
  const [fromBrowser, setFromBrowser] = useState<Suggestion | null>(null);
  useEffect(() => {
    if (chosen !== null || fromServer !== null) return;
    const region = regionFromLanguages(navigator.languages?.length ? navigator.languages : [navigator.language]);
    if (!region) return;
    let alive = true;
    api.billing
      .routeFor(region)
      .then((route) => alive && setFromBrowser({ country: region, route: { currency: route.currency, provider: route.provider }, from: "your browser's language" }))
      .catch(() => undefined); // a country with no route is simply not suggested
    return () => {
      alive = false;
    };
  }, [chosen, fromServer]);
  const suggestion = chosen === null ? (fromServer ?? fromBrowser) : null;
  const showSuggestion = suggestion !== null && !changing;
  const picking = chosen === null ? !showSuggestion : editing;

  // The country in front of the person that is not saved, told to the plans screen.
  const candidate = chosen !== null ? null : showSuggestion ? suggestion.country : picked || null;
  useEffect(() => {
    onCandidate?.(candidate);
  }, [candidate, onCandidate]);

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

  const shown = useMemo(() => filterCountryOptions(query), [query]);
  const waitingFor = profile.pending_billing_country;
  const allowedAt = profile.country_change_allowed_at;
  const tooSoon = allowedAt !== null && new Date(allowedAt).getTime() > now;

  async function save(country: string) {
    if (!country || busy) return;
    setBusy(true);
    setError(null);
    try {
      const answer = await api.billing.setCountry(country);
      onChange(answer.profile);
      setEditing(false);
      setChanging(false);
      setPicked("");
      setQuery("");
      flash(
        answer.result === "scheduled"
          ? "Your change will take effect when your paid month ends"
          : answer.result === "unchanged"
            ? "That is already your billing country"
            : "Billing country saved",
      );
    } catch (caught) {
      setError(billingErrorMessage(caught));
      // A refusal means what the card knew is out of date (another tab, another device): read it again.
      onChange(profile);
    } finally {
      setBusy(false);
    }
  }

  function startPicking(first: string) {
    setPicked(first);
    setQuery("");
    setError(null);
  }

  return (
    <section className="card bill-card" aria-labelledby="bill-country-title">
      <div>
        <h2 id="bill-country-title" className="set-title">
          Billing country
        </h2>
        <p className="set-sub">
          This sets your currency. Use the country your business is based in.
        </p>
      </div>

      {chosen && !editing ? (
        <div className="bill-current">
          <div className="pl-row-title">{countryName(chosen)}</div>
          {profile.route ? <div className="pl-row-sub">{routeStatement(profile.route)}</div> : null}
          <div className="pl-row-sub">
            {!profile.country_locked
              ? "Until your first payment you can change this country as often as you like."
              : tooSoon && allowedAt
                ? null
                : "Since your first payment it can be changed once every 30 days."}
          </div>
        </div>
      ) : null}

      {waitingFor && profile.pending_effective_at ? (
        <Alert kind="info">
          Your billing country changes to {countryName(waitingFor)} on <LocalTime iso={profile.pending_effective_at} kind="day" />,
          when your paid month ends. Until then you keep paying as you do now, because your plan was bought on that route and a
          saved card cannot be moved. A waiting change cannot be undone.
        </Alert>
      ) : null}

      {showSuggestion && suggestion ? (
        <div className="bill-suggest">
          <div className="pl-row-title">{countryName(suggestion.country)}</div>
          <div className="bill-statement" aria-live="polite">
            <strong>{routeStatement(suggestion.route)}</strong>
          </div>
          <p className="pl-row-sub">
            We picked this from {suggestion.from}. It is not saved until you confirm it here, or go on to pay for a plan.
          </p>
          <div className="pl-controls">
            <button type="button" className="btn btn-primary pl-btn" disabled={busy} onClick={() => void save(suggestion.country)}>
              {busy ? "Saving..." : `Use ${countryName(suggestion.country)}`}
            </button>
            <button
              type="button"
              className="btn btn-quiet pl-btn"
              disabled={busy}
              onClick={() => {
                setChanging(true);
                startPicking(suggestion.country);
              }}
            >
              Not {countryName(suggestion.country)}? Change
            </button>
          </div>
          {error ? <Alert kind="error">{error}</Alert> : null}
        </div>
      ) : null}

      {picking ? (
        <div className="bill-form">
          <Field
            id="bill-country-search"
            label="Search countries"
            type="search"
            autoComplete="off"
            placeholder="Type a country name"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <div className="field">
            <label className="field-label" htmlFor="bill-country">
              Billing country
            </label>
            <select className="select" id="bill-country" name="bill-country" value={picked} onChange={(event) => setPicked(event.target.value)}>
              <option value="">{shown.all.length === 0 ? "No country matches" : "Choose a country"}</option>
              {shown.common.length > 0 ? (
                <optgroup label="Common">
                  {shown.common.map(([code, name]) => (
                    <option key={`common-${code}`} value={code}>
                      {name}
                    </option>
                  ))}
                </optgroup>
              ) : null}
              <optgroup label={shown.common.length > 0 ? "All countries" : "Matching countries"}>
                {shown.all.map(([code, name]) => (
                  <option key={code} value={code}>
                    {name}
                  </option>
                ))}
              </optgroup>
            </select>
          </div>
          <div aria-live="polite" className="bill-statement">
            {picked && preview ? <strong>{routeStatement(preview)}</strong> : null}
            {previewError ? <Alert kind="error">{previewError}</Alert> : null}
          </div>
          <div className="pl-controls">
            <button type="button" className="btn btn-primary pl-btn" disabled={!picked || busy} onClick={() => void save(picked)}>
              {busy ? "Saving..." : "Save country"}
            </button>
            {chosen || suggestion ? (
              <button
                type="button"
                className="btn btn-secondary pl-btn"
                disabled={busy}
                onClick={() => {
                  setEditing(false);
                  setChanging(false);
                  setPicked("");
                  setQuery("");
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
          <button
            type="button"
            className="btn btn-secondary pl-btn"
            disabled={tooSoon}
            onClick={() => {
              startPicking(chosen);
              setEditing(true);
            }}
          >
            Change country
          </button>
          {tooSoon && allowedAt ? (
            <span className="pl-row-sub">
              Since your first payment it can be changed once every 30 days. You can change it again on{" "}
              <LocalTime iso={allowedAt} kind="day" />.
            </span>
          ) : null}
        </div>
      ) : null}

      {/* Said in full while no country is chosen yet; folded away once one is, so the plans are not pushed down a phone. */}
      <details className="bill-more" open={!chosen}>
        <summary>How the billing country works</summary>
        <ul className="bill-rules">
          <li>Until your first payment you can change it as often as you like, and it takes effect at once.</li>
          <li>After your first payment you can change it once every 30 days.</li>
          <li>
            If you already have a plan and you move between Nigeria and another country, the change waits until your paid
            month ends. You then pay again on the new route, because a saved card cannot be moved.
          </li>
          <li>
            You can pay with a card from another country, but your bank may decline it. If you are billed in US dollars, use a
            card your bank lets you spend in dollars. If you are established in Nigeria, choose Nigeria so you pay in naira.
          </li>
        </ul>
      </details>
      <Toast message={toast} />
    </section>
  );
}
