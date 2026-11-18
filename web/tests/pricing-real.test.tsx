import { readdirSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { GET as llms } from "@/app/llms.txt/route";
import sitemap from "@/app/sitemap";
import robots from "@/app/robots";
import PricingRoute, { metadata as pricingMetadata } from "@/app/(public)/pricing/page";
import { Pricing } from "@/components/pricing/pricing";
import {
  CATALOG,
  COMPARED,
  FAQS,
  HERO,
  PACK_ENTRIES,
  PLANS,
  PLAN_ENTRIES,
  TRIAL,
  ngn,
  offers,
  priceText,
  tableRows,
  usd,
  type Sellable,
} from "@/components/pricing/content";
import { organizationJsonLd, softwareJsonLd } from "@/components/site/json-ld";
import { PublicShell } from "@/components/site/public-shell";
import { read, REPO } from "./helpers";
import { renderPublic } from "./public-render";

const state = vi.hoisted(() => ({
  session: { authenticated: false } as { authenticated: boolean },
  billing: vi.fn(async () => ({}) as unknown),
}));
vi.mock("@/lib/api/server", () => ({
  getSession: async () => state.session,
  serverApi: async () => ({ billing: { state: state.billing } }),
}));

// The pricing page says only what is true: every price and hour count it prints is the one the database is
// seeded with (the Alembic migrations, first written in 0052_subscriptions.py), the trial is the one decided in the
// spec, and nothing the lawyer has not read is promised. EVERY entry of CATALOG is checked against the seed, so a
// plan or pack added to the page without its seed row (or with a different number) fails here. If a price changes,
// the migration and components/pricing/content.ts change together
// (docs/sop/2026-11-17-pricing-real-and-billing-nav.md).

const text = (html: string) => html.replace(/<[^>]*>/g, " ").replace(/&#x27;|&apos;/g, "'").replace(/\s+/g, " ");
// Every migration, in order: a plan, price or pack added later is found in the migration that adds it, and the
// LAST row for a code wins (a price change is a new row).
const migrations = readdirSync(join(REPO, "alembic/versions"))
  .filter((name) => /^\d{4}_.*\.py$/.test(name))
  .sort()
  .map((name) => read(join(REPO, "alembic/versions", name)))
  .join("\n");
const config = read(join(REPO, "app/config.py"));

const minor = (raw: string): number => Number(raw.replaceAll("_", ""));
const last = <T,>(rows: T[], what: string): T => {
  const row = rows[rows.length - 1];
  if (!row) throw new Error(`the migrations have no ${what}: the page cannot be checked against them`);
  return row;
};
const escape = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// What the migrations seed for one catalog entry: its name, hours, validity and a price per currency.
function seeded(entry: Sellable) {
  const code = escape(entry.code);
  if (entry.kind === "plan") {
    const plan = last(
      [...migrations.matchAll(new RegExp(`"code": "${code}",\\s*"name": "([^"]+)",\\s*"monthly_seconds": (\\d+) \\* 3600`, "g"))],
      `plan ${entry.code}`,
    );
    const price = (currency: string) =>
      minor(
        last(
          [...migrations.matchAll(new RegExp(`"plan_code": "${code}",\\s*"currency": "${currency}",\\s*"interval": "month",\\s*"amount_minor": ([\\d_]+)`, "g"))],
          `${entry.code} price in ${currency}`,
        )[1],
      );
    return { name: plan[1], hours: Number(plan[2]), months: undefined, usd: price("USD"), ngn: price("NGN") };
  }
  const product = (currency: string) =>
    last(
      [
        ...migrations.matchAll(
          new RegExp(
            `"code": "${code}",\\s*"name": "([^"]+)",\\s*"currency": "${currency}",\\s*"seconds": (\\d+) \\* 3600,\\s*"amount_minor": ([\\d_]+),\\s*"valid_months": (\\d+)`,
            "g",
          ),
        ),
      ],
      `pack ${entry.code} in ${currency}`,
    );
  const usdRow = product("USD");
  const ngnRow = product("NGN");
  return { name: usdRow[1], hours: Number(usdRow[2]), months: Number(usdRow[4]), usd: minor(usdRow[3]), ngn: minor(ngnRow[3]), ngnName: ngnRow[1] };
}

const html = async (signedIn = false, billingOn = false) => {
  state.session = { authenticated: signedIn };
  return renderPublic(
    <PublicShell>
      <Pricing signedIn={signedIn} billingOn={billingOn} />
    </PublicShell>,
  );
};
const pageHtml = await html();
const page = text(pageHtml);
// The page's own words: everything but the shared header and footer.
const body = text(pageHtml.slice(pageHtml.indexOf("</header>"), pageHtml.indexOf("<footer")));

describe("the pricing page against the seed", () => {
  it.each(CATALOG.map((entry) => [entry.code, entry] as const))(
    "%s: name, hours, validity and both prices are exactly what the migrations seed, and the page prints them",
    (_code, entry) => {
      const row = seeded(entry);
      expect(row.name).toBe(entry.name);
      expect(row.hours).toBe(entry.hours);
      expect(row.usd / 100).toBe(entry.usdMajor);
      expect(row.ngn / 100).toBe(entry.ngnMajor);
      if (entry.kind === "pack") {
        expect(row.months).toBe(entry.validMonths);
        expect(seeded(entry).ngnName).toBe(entry.name);
      } else {
        expect(entry.validMonths).toBeUndefined();
      }
      // The same figures as a person reads them.
      expect(usd(row.usd / 100)).toBe(`$${entry.usdMajor}`);
      expect(ngn(row.ngn / 100)).toBe(priceText(entry).split(" or ")[1]);
      for (const printed of [entry.name, usd(entry.usdMajor), ngn(entry.ngnMajor)]) expect(body).toContain(printed);
      if (entry.kind === "pack") expect(body).toContain(`${entry.validMonths} months`);
    },
  );

  it("flows every catalog entry through the cards, the table, the page text, llms.txt and the JSON-LD (one array)", () => {
    expect(PLANS).toHaveLength(1 + CATALOG.length);
    expect(PLANS.slice(1).map((p) => p.id)).toEqual([...PLAN_ENTRIES, ...PACK_ENTRIES].map((s) => s.code));
    expect(PLANS.filter((p) => p.main)).toHaveLength(1);
    expect(COMPARED).toHaveLength(1 + PLAN_ENTRIES.length);
    for (const row of tableRows()) expect(row, row[0]).toHaveLength(1 + COMPARED.length);
    expect(offers("https://x.test")).toHaveLength(2 * CATALOG.length);
    expect(softwareJsonLd().offers).toEqual(offers("https://notes.beyrictech.com"));
  });

  it("is `is_public` false in the seed: the page, not the API, prints the plan", () => {
    expect(migrations).toContain('"is_public": False');
  });

  it("gives the trial the length the code and the deploy notes give it (3 hours is TRIAL_SECONDS=10800 on the server)", () => {
    const days = last([...config.matchAll(/trial_days: int = Field\(default=(\d+)/g)], "trial_days")[1];
    expect(days).toBe(String(TRIAL.days));
    // The size of the trial is a server setting, off (0) by default; the code's own comment and the deploy README
    // name the value that means 3 hours, and the page's 3 is only true while the server sets that value.
    expect(last([...config.matchAll(/trial_seconds: int = Field\(default=(\d+)/g)], "trial_seconds")[1]).toBe("0");
    const configHours = Number(last([...config.matchAll(/(\d+) is 3 hours/g)], "the trial size in app/config.py")[1]);
    expect(configHours).toBe(TRIAL.hours * 3600);
    expect(read(join(REPO, "deploy/README.md"))).toContain(`\`${configHours}\` = 3 hours`);
    expect(body).toContain(`${TRIAL.hours} hours`);
    expect(body).toContain(`${TRIAL.days} days`);
  });

  it("prints no number that is not one of the decided ones", () => {
    const allowed = new Set<string>([String(TRIAL.hours), String(TRIAL.days)]);
    for (const s of CATALOG) {
      allowed.add(String(s.hours));
      allowed.add(String(s.usdMajor));
      allowed.add(s.ngnMajor.toLocaleString("en-US"));
      if (s.validMonths) allowed.add(String(s.validMonths));
    }
    const printed = [...body.matchAll(/\d[\d,]*\d|\d/g)].map((m) => m[0]);
    expect(printed.length).toBeGreaterThan(10);
    for (const n of printed) expect(allowed.has(n), `"${n}" is printed and is not a decided number`).toBe(true);
  });
});

describe("the pricing page says what is decided and nothing else", () => {
  it("has the trial, the monthly plans and the packs, and no plan that does not exist", () => {
    expect(PLANS.map((p) => p.name)).toEqual(["Trial", ...CATALOG.map((s) => s.name)]);
    expect(PLANS.map((p) => p.name)).toEqual(["Trial", "20 hours a month", "Pro", "Scale", "10 extra hours"]);
    expect(page).not.toMatch(/team plus|to be announced|no plan is on sale|not yet|start with the trial/i);
    expect(page).not.toMatch(/business|\bfree\b|unlimited|forever|hubspot|jira|asana|most teams pick/i);
    expect(pageHtml.match(/class="ld-plan"|class="ld-plan ld-plan-main"/g)).toHaveLength(PLANS.length);
  });

  it("says who pays in which currency, through which provider, without implying a conversion", () => {
    expect(body).toContain("People in Nigeria pay in naira through Paystack");
    expect(body).toContain("Everywhere else pays in US dollars through Flutterwave");
    expect(body).toContain("not a conversion");
    expect(body).not.toMatch(/about|roughly|approximately|equivalent|≈/i);
  });

  it("says the hours belong to the person, never that a team shares them, and that unused hours do not roll over", () => {
    expect(body).toContain("Hours belong to the person");
    expect(body).toContain("they belong to the person who has the plan, not to a team");
    expect(body).toContain("do not roll over");
    expect(body).not.toMatch(/whole team|shared by|shared across|share[sd]? (the )?hours|pooled/i);
    expect(pricingMetadata.description).toContain("belong to the person");
  });

  it("keeps the comparison honest: the trial and every plan, every feature row 'Yes' everywhere, packs 'No' for the trial", () => {
    expect(COMPARED.map((p) => p.name)).toEqual(["Trial", ...PLAN_ENTRIES.map((s) => s.name)]);
    const rows = tableRows();
    const features = rows.slice(5);
    expect(features.length).toBeGreaterThan(8);
    for (const [label, ...cells] of features) expect(cells, label).toEqual(COMPARED.map(() => "Yes"));
    expect(rows.find(([label]) => label.startsWith("Extra-hour packs"))?.slice(1)).toEqual(["No", ...PLAN_ENTRIES.map(() => "Yes")]);
    expect(rows.map((r) => r[0])).toEqual(expect.arrayContaining(["Calendar auto-join", "Tasks, projects, sprints and milestones", "Templates and vocabulary"]));
    expect(body).not.toMatch(/\bTeam\b/);
  });

  it("answers cancelling (access to the period's end) and prints no refund wording: the lawyer has not read it", () => {
    const answers = FAQS.map((f) => `${f.q} ${f.a}`).join(" ");
    expect(answers).toContain("keeps working until the end of the period you paid for");
    expect(page).not.toMatch(/refund/i);
    expect(pricingMetadata.description).not.toMatch(/refund/i);
    // The refund windows, the failed-payment grace, what happens when payment stops, notice promises: not printed.
    expect(page).not.toMatch(/within \d+ days of|money.?back|guarantee|refund window|grace|past due|lapse|we will (tell|notify|email)|notice of/i);
    expect(page).not.toMatch(/7 days|14 days of the payment/i);
    expect(HERO.body + answers).not.toMatch(/lawyer|legal advice|terms of service/i);
  });

  it("says when the hours run out what a trial person can do too: add a pack, choose a plan or renew", () => {
    expect(body).toContain("wait until you add a pack, choose a plan or your plan renews");
  });

  it("starts at sign-up for a visitor, at the plans screen for a signed-in person billing answers for, and at the app for one it does not", async () => {
    expect(pageHtml.match(/class="ld-plan-cta" href="\/signup"/g)).toHaveLength(PLANS.length);
    expect(page).toContain("Get started");
    const on = await html(true, true);
    expect(on.match(/class="ld-plan-cta" href="\/billing\/plans"/g)).toHaveLength(PLANS.length);
    expect(on).toContain('class="btn btn-xl ld-create" href="/billing/plans"');
    expect(text(on)).toContain("Choose a plan");
    expect(on.match(/class="ld-plan-cta" href="\/signup"/g)).toBeNull();
    // Billing off for this person: no dead end at "Plans are not available for your account yet".
    const off = await html(true, false);
    expect(off.match(/class="ld-plan-cta" href="\/"/g)).toHaveLength(PLANS.length);
    expect(off).not.toContain('href="/billing/plans"');
    expect(text(off)).toContain("Open the app");
  });

  it("asks billing from the server for a signed-in person only, and a refusal or failure means the app, not the plans", async () => {
    const cta = async () => text(renderToStaticMarkup(await PricingRoute()));
    state.billing.mockClear();
    state.session = { authenticated: false };
    expect(await cta()).toContain("Get started");
    expect(state.billing).not.toHaveBeenCalled();
    state.session = { authenticated: true };
    state.billing.mockResolvedValueOnce({});
    expect(await cta()).toContain("Choose a plan");
    state.billing.mockRejectedValueOnce(new Error("not_found"));
    const refused = await cta();
    expect(refused).toContain("Open the app");
    expect(refused).not.toContain("Choose a plan");
    state.session = { authenticated: false };
  });

  it("sits inside the shared public header and footer, with our entity and mailbox", () => {
    expect(page).toContain("Who it's for");
    expect(page).toContain("© 2026 Beyrichq LTD");
    expect(pageHtml).toContain('href="mailto:privacy@beyrictech.com"');
    expect(pageHtml).not.toContain("hello@");
  });

  it("has its own metadata, a canonical, a place in the sitemap and in robots.txt, with the real prices in the description", async () => {
    expect(pricingMetadata.alternates?.canonical).toBe("/pricing");
    for (const s of CATALOG) for (const t of [usd(s.usdMajor), ngn(s.ngnMajor), `${s.hours} hours`]) expect(pricingMetadata.description).toContain(t);
    expect(pricingMetadata.description).toContain(`${TRIAL.hours} hours`);
    state.session = { authenticated: false };
    expect(text(renderToStaticMarkup(await PricingRoute()))).toContain("Pay when your team runs on it.");
    expect(sitemap().map((e) => e.url.replace(/^https?:\/\/[^/]+/, ""))).toContain("/pricing");
    expect(robots().rules).toEqual([expect.objectContaining({ allow: expect.arrayContaining(["/pricing"]) })]);
  });

  it("repeats the same numbers in llms.txt and, for every plan and pack in both currencies, in the structured data", async () => {
    const llmsText = await llms().text();
    expect(llmsText).not.toMatch(/to be announced|no plan is on sale/i);
    for (const s of CATALOG) for (const t of [usd(s.usdMajor), ngn(s.ngnMajor), `${s.hours} hours`]) expect(llmsText).toContain(t);
    for (const t of ["Paystack", "Flutterwave"]) expect(llmsText).toContain(t);
    const software = softwareJsonLd();
    // Every offer is a seeded price: the amount in major units and its currency.
    for (const o of software.offers) {
      const entry = CATALOG.find((s) => s.name === o.name);
      expect(entry, o.name).toBeDefined();
      const row = seeded(entry as Sellable);
      expect(Number(o.price) * 100, `${o.name} ${o.priceCurrency}`).toBe(o.priceCurrency === "USD" ? row.usd : row.ngn);
    }
    expect(software.offers).toHaveLength(2 * CATALOG.length);
    // A monthly plan says it is monthly; a pack is a one-off and says nothing of a period.
    for (const o of software.offers) {
      const entry = CATALOG.find((s) => s.name === o.name) as Sellable;
      if (entry.kind === "plan") {
        expect(o.priceSpecification).toEqual({ "@type": "UnitPriceSpecification", price: o.price, priceCurrency: o.priceCurrency, billingDuration: "P1M" });
      } else {
        expect(o.priceSpecification).toBeUndefined();
      }
    }
    expect(JSON.stringify(organizationJsonLd())).not.toContain("Offer");
  });
});
