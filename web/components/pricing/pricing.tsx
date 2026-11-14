import { AppLink } from "@/components/ui";
import { FaqList, LandingHeader, PricingFooter, SignupNote } from "@/components/landing/landing";
import {
  CLOSING_TITLE,
  FAQS,
  HERO,
  PLANS,
  TABLE_NOTE,
  TABLE_TITLE,
  hoursLine,
  priceOf,
  tableRows,
  type Plan,
} from "./content";

// The pricing page from the designer's Pricing file (2026-11-14): three plan cards, the comparison table,
// the questions and the teal closing band. The words and numbers are all in content.ts; every price and
// number of hours there is still to be announced until Adebayo decides them.

function PlanCard({ plan }: { plan: Plan }) {
  const decided = plan.price !== null;
  return (
    <div className={`ld-plan${plan.main ? " ld-plan-main" : ""}`}>
      <div className="ld-plan-head">
        <h2>{plan.name}</h2>
        <p>{plan.tagline}</p>
      </div>
      <div className="ld-plan-price">
        <span className={`ld-plan-amount${decided ? " ld-plan-amount-set" : ""}`}>{priceOf(plan)}</span>
        <span className="ld-plan-unit">{plan.priceUnit}</span>
      </div>
      <AppLink className="ld-plan-cta" href="/signup">
        {plan.cta}
      </AppLink>
      <ul className="ld-plan-list">
        <li className="ld-plan-lead">{hoursLine(plan)}</li>
        {plan.includes.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

function Table() {
  const rows = tableRows(PLANS);
  return (
    <section className="ld-compare-section">
      <div className="ld-compare-in">
        <h2 className="ld-h2-table">{TABLE_TITLE}</h2>
        <div className="ld-table-wrap">
          <div className="ld-table" role="table" aria-label="Plan comparison">
            <div className="ld-table-head" role="row">
              <span role="columnheader">Feature</span>
              {PLANS.map((plan) => (
                <span key={plan.id} role="columnheader" className={plan.main ? "ld-col-main" : undefined}>
                  {plan.name}
                </span>
              ))}
            </div>
            {rows.map(([label, ...cells]) => (
              <div key={label} role="row">
                <span role="rowheader">{label}</span>
                {cells.map((cell, index) => (
                  <span key={PLANS[index].id} role="cell" className={PLANS[index].main ? "ld-col-main" : undefined}>
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

export function Pricing() {
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
          <PlanCard key={plan.id} plan={plan} />
        ))}
      </section>
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
          <AppLink className="btn btn-xl ld-create" href="/signup">
            Create your account
          </AppLink>
        </div>
      </section>
    </>
  );
}

// The whole pricing page, header and footer included. `signedIn` is the same as on the landing page.
export function PricingPage({ signedIn = false }: { signedIn?: boolean }) {
  return (
    <div className="ld">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <LandingHeader signedIn={signedIn} page="pricing" />
      <main id="main">
        <Pricing />
      </main>
      <PricingFooter signedIn={signedIn} />
    </div>
  );
}
