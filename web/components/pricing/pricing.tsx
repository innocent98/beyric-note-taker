import type { CSSProperties } from "react";
import { AppLink } from "@/components/ui";
import { FaqList, SignupNote } from "@/components/landing/landing";
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

function PlanCard({ plan, signedIn }: { plan: Plan; signedIn: boolean }) {
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
      <AppLink className="ld-plan-cta" href={signedIn ? BILLING_PLANS_HREF : "/signup"}>
        {signedIn ? "Choose a plan" : "Get started"}
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
          <div className="ld-table ld-table-2" role="table" aria-label="Plan comparison" style={{ "--plans": COMPARED.length } as CSSProperties}>
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

export function Pricing({ signedIn = false }: { signedIn?: boolean }) {
  return (
    <>
      <section className="ld-phero">
        <span className="ld-eyebrow">{HERO.eyebrow}</span>
        <h1>{HERO.title}</h1>
        <p>{HERO.body}</p>
        <SignupNote />
      </section>
      <section className="ld-plans" aria-label="Plans">
        {PLANS.map((plan) => (
          <PlanCard key={plan.id} plan={plan} signedIn={signedIn} />
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
            <SignupNote className="ld-signup-note-on-teal" />
          </div>
          <AppLink className="btn btn-xl ld-create" href={signedIn ? BILLING_PLANS_HREF : "/signup"}>
            {signedIn ? "Choose a plan" : "Create your account"}
          </AppLink>
        </div>
      </section>
    </>
  );
}
