import { AppLink } from "@/components/ui";
import { FaqList } from "@/components/landing/landing";
import { BILLING_PLANS_HREF } from "@/lib/billing";
import {
  CLOSING_TITLE,
  COMPARED,
  CURRENCY_NOTE,
  FAQS,
  HERO,
  PLANS,
  TABLE_NOTE,
  TABLE_TITLE,
  tableRows,
  type Plan,
} from "./content";

// The pricing page from the designer's Pricing file (2026-11-14): three cards (the trial, the monthly plan, the
// 10-hour pack), the comparison table, the questions and the teal closing band. The words and numbers are all in
// content.ts, which web/tests/pricing-real.test.tsx pins to the seeded prices. `signedIn`: the buttons lead a
// signed-in person to the plans screen, everyone else to sign-up.

// Where the buttons lead. A person for whom billing answers goes to the plans screen; a signed-in person billing does
// not reach (switched off, or not on the allow-list) would land on a dead end there, so goes to the app; a visitor
// signs up.
type Entry = { href: string; label: string; closing: string };
function entryFor(signedIn: boolean, billingOn: boolean): Entry {
  if (signedIn && billingOn) return { href: BILLING_PLANS_HREF, label: "Choose a plan", closing: "Choose a plan" };
  if (signedIn) return { href: "/", label: "Open the app", closing: "Open the app" };
  return { href: "/signup", label: "Get started", closing: "Create your account" };
}

function PlanCard({ plan, entry }: { plan: Plan; entry: Entry }) {
  return (
    <div className={`ld-plan${plan.main ? " ld-plan-main" : ""}`}>
      <div className="ld-plan-head">
        <h2>{plan.name}</h2>
        <p>{plan.tagline}</p>
      </div>
      <div className="ld-plan-price">
        <span className="ld-plan-amount ld-plan-amount-set">{plan.amount}</span>
        <span className="ld-plan-unit">{plan.unit}</span>
        {plan.alt ? <span className="ld-plan-alt">{plan.alt}</span> : null}
      </div>
      <AppLink className="ld-plan-cta" href={entry.href}>
        {entry.label}
      </AppLink>
      <ul className="ld-plan-list">
        <li className="ld-plan-lead">{plan.hoursLine}</li>
        {plan.includes.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

function Table() {
  const rows = tableRows();
  return (
    <section className="ld-compare-section">
      <div className="ld-compare-in">
        <h2 className="ld-h2-table">{TABLE_TITLE}</h2>
        <div className="ld-table-wrap">
          <div className={`ld-table ld-table-${COMPARED.length}`} role="table" aria-label="Plan comparison">
            <div className="ld-table-head" role="row">
              <span role="columnheader">Feature</span>
              {COMPARED.map((plan) => (
                <span key={plan.id} role="columnheader" className={plan.main ? "ld-col-main" : undefined}>
                  {plan.name}
                </span>
              ))}
            </div>
            {rows.map(([label, ...cells]) => (
              <div key={label} role="row">
                <span role="rowheader">{label}</span>
                {cells.map((cell, index) => (
                  <span key={COMPARED[index].id} role="cell" className={COMPARED[index].main ? "ld-col-main" : undefined}>
                    {cell}
                  </span>
                ))}
              </div>
            ))}
          </div>
        </div>
        <p className="ld-table-note">{TABLE_NOTE}</p>
      </div>
    </section>
  );
}

export function Pricing({ signedIn = false, billingOn = false }: { signedIn?: boolean; billingOn?: boolean }) {
  const entry = entryFor(signedIn, billingOn);
  return (
    <>
      <section className="ld-phero">
        <span className="ld-eyebrow">{HERO.eyebrow}</span>
        <h1>{HERO.title}</h1>
        <p>{HERO.body}</p>
      </section>
      <section className="ld-plans" aria-label="Plans">
        {PLANS.map((plan) => (
          <PlanCard key={plan.id} plan={plan} entry={entry} />
        ))}
      </section>
      <p className="ld-plans-note">{CURRENCY_NOTE}</p>
      <Table />
      <section className="ld-pfaq">
        <h2 className="ld-h2-table">Pricing questions</h2>
        <FaqList items={FAQS} />
      </section>
      <section className="ld-pclosing">
        <div className="ld-closing-in">
          <div className="ld-closing-copy">
            <h2 className="ld-h2">{CLOSING_TITLE}</h2>
          </div>
          <AppLink className="btn btn-xl ld-create" href={entry.href}>
            {entry.closing}
          </AppLink>
        </div>
      </section>
    </>
  );
}
