import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { GET as llms } from "@/app/llms.txt/route";
import sitemap from "@/app/sitemap";
import robots from "@/app/robots";
import PricingRoute, { metadata as pricingMetadata } from "@/app/(public)/pricing/page";
import { Pricing } from "@/components/pricing/pricing";
import { COMPARED, FAQS, HERO, PACK, PLAN, PLANS, TRIAL, tableRows } from "@/components/pricing/content";
import { organizationJsonLd, softwareJsonLd } from "@/components/site/json-ld";
import { PublicShell } from "@/components/site/public-shell";
import { read, REPO } from "./helpers";
import { renderPublic } from "./public-render";

const state = vi.hoisted(() => ({ session: { authenticated: false } as { authenticated: boolean } }));
vi.mock("@/lib/api/server", () => ({ getSession: async () => state.session }));

// The pricing page says only what is true: every price and hour count it prints is the one the database is
// seeded with (alembic/versions/0052_subscriptions.py), the trial is the one decided in the spec, and nothing the
// lawyer has not read is promised. If a price changes, the seed and components/pricing/content.ts change
// together, and this test is what tells whichever was forgotten (docs/sop/2026-11-17-pricing-real-and-billing-nav.md).

const text = (html: string) => html.replace(/<[^>]*>/g, " ").replace(/&#x27;|&apos;/g, "'").replace(/\s+/g, " ");
const seed = read(join(REPO, "alembic/versions/0052_subscriptions.py"));
const config = read(join(REPO, "app/config.py"));

const must = (m: RegExpMatchArray | null, what: string): RegExpMatchArray => {
  if (!m) throw new Error(`the seed has no ${what}: the page cannot be checked against it`);
  return m;
};
const minor = (raw: string): number => Number(raw.replaceAll("_", ""));
const dollars = (cents: number): string => `$${cents / 100}`;
const naira = (kobo: number): string => `₦${(kobo / 100).toLocaleString("en-US")}`;

const planSeed = (currency: "USD" | "NGN") =>
  minor(must(seed.match(new RegExp(`"plan_code": "plan_20h",\\s*"currency": "${currency}",\\s*"interval": "month",\\s*"amount_minor": ([\\d_]+)`)), `plan_20h price in ${currency}`)[1]);
const packSeed = (currency: "USD" | "NGN") => {
  const m = must(
    seed.match(
      new RegExp(`"code": "pack_10h",\\s*"name": "([^"]+)",\\s*"currency": "${currency}",\\s*"seconds": (\\d+) \\* 3600,\\s*"amount_minor": ([\\d_]+),\\s*"valid_months": (\\d+)`),
    ),
    `pack_10h in ${currency}`,
  );
  return { name: m[1], hours: Number(m[2]), amount: minor(m[3]), months: Number(m[4]) };
};

const html = async (signedIn = false) => {
  state.session = { authenticated: signedIn };
  return renderPublic(
    <PublicShell>
      <Pricing signedIn={signedIn} />
    </PublicShell>,
  );
};
const pageHtml = await html();
const page = text(pageHtml);
// The page's own words: everything but the shared header and footer.
const body = text(pageHtml.slice(pageHtml.indexOf("</header>"), pageHtml.indexOf("<footer")));

describe("the pricing page against the seed", () => {
  it("prints the plan's name, hours and both prices exactly as the migration seeds them", () => {
    expect(must(seed.match(/"code": "plan_20h",\s*"name": "([^"]+)"/), "plan name")[1]).toBe(PLAN.name);
    expect(Number(must(seed.match(/"monthly_seconds": (\d+) \* 3600/), "plan hours")[1])).toBe(PLAN.hours);
    expect(dollars(planSeed("USD"))).toBe(PLAN.usd);
    expect(naira(planSeed("NGN"))).toBe(PLAN.ngn);
    expect(planSeed("USD") / 100).toBe(PLAN.usdMajor);
    expect(planSeed("NGN") / 100).toBe(PLAN.ngnMajor);
    for (const text of [PLAN.name, PLAN.usd, PLAN.ngn]) expect(body).toContain(text);
  });

  it("prints the pack's name, hours, validity and both prices exactly as the migration seeds them", () => {
    const usd = packSeed("USD");
    const ngn = packSeed("NGN");
    for (const row of [usd, ngn]) {
      expect(row.name).toBe(PACK.name);
      expect(row.hours).toBe(PACK.hours);
      expect(row.months).toBe(PACK.months);
    }
    expect(dollars(usd.amount)).toBe(PACK.usd);
    expect(naira(ngn.amount)).toBe(PACK.ngn);
    expect(usd.amount / 100).toBe(PACK.usdMajor);
    expect(ngn.amount / 100).toBe(PACK.ngnMajor);
    for (const text of [PACK.name, PACK.usd, PACK.ngn, `${PACK.months} months`]) expect(body).toContain(text);
  });

  it("is public in the seed: the plan is `is_public` false there because the page, not the API, prints it", () => {
    expect(seed).toContain('"is_public": False');
  });

  it("gives the trial the length the code gives it (3 hours is TRIAL_SECONDS=10800 on the server)", () => {
    expect(must(config.match(/trial_days: int = Field\(default=(\d+)/), "trial_days")[1]).toBe(String(TRIAL.days));
    expect(TRIAL.hours * 3600).toBe(10800);
    expect(body).toContain(`${TRIAL.hours} hours`);
    expect(body).toContain(`${TRIAL.days} days`);
  });

  it("prints no number that is not one of the decided ones", () => {
    const allowed = new Set(
      [TRIAL.hours, TRIAL.days, PLAN.hours, PLAN.usdMajor, PACK.hours, PACK.usdMajor, PACK.months].map(String).concat(["25,000", "15,000"]),
    );
    const printed = [...body.matchAll(/\d[\d,]*\d|\d/g)].map((m) => m[0]);
    expect(printed.length).toBeGreaterThan(10);
    for (const n of printed) expect(allowed.has(n), `"${n}" is printed and is not a decided number`).toBe(true);
  });
});

describe("the pricing page says what is decided and nothing else", () => {
  it("has the trial, the one monthly plan and the pack, and no plan that does not exist", () => {
    expect(PLANS.map((p) => p.name)).toEqual(["Trial", "20 hours a month", "10 extra hours"]);
    expect(page).not.toMatch(/team plus|to be announced|no plan is on sale|not yet|start with the trial/i);
    expect(page).not.toMatch(/business|\bfree\b|unlimited|forever|hubspot|jira|asana|most teams pick/i);
    expect(pageHtml.match(/class="ld-plan"|class="ld-plan ld-plan-main"/g)).toHaveLength(3);
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

  it("keeps the comparison honest: two columns, every feature row 'Yes' on both, the packs row says No for the trial", () => {
    expect(COMPARED.map((p) => p.name)).toEqual(["Trial", "20 hours a month"]);
    const rows = tableRows();
    const features = rows.slice(5);
    expect(features.length).toBeGreaterThan(8);
    for (const [label, ...cells] of features) expect(cells, label).toEqual(["Yes", "Yes"]);
    expect(rows.find(([label]) => label.startsWith("Extra-hour packs"))?.slice(1)).toEqual(["No", "Yes"]);
    expect(rows.map((r) => r[0])).toEqual(expect.arrayContaining(["Calendar auto-join", "Tasks, projects, sprints and milestones", "Templates and vocabulary"]));
    expect(body).not.toMatch(/\bTeam\b/);
  });

  it("answers cancelling (access to the period's end, no refund of the unused part) and leaves out what the lawyer has not read", () => {
    const answers = FAQS.map((f) => `${f.q} ${f.a}`).join(" ");
    expect(answers).toContain("keeps working until the end of the period you paid for");
    expect(answers).toContain("The unused part of the period is not refunded");
    // The refund windows, the failed-payment grace, what happens when payment stops, notice promises: not printed.
    expect(page).not.toMatch(/within \d+ days of|money.?back|guarantee|refund window|grace|past due|lapse|we will (tell|notify|email)|notice of/i);
    expect(page).not.toMatch(/7 days|14 days of the payment/i);
    expect(HERO.body + answers).not.toMatch(/lawyer|legal advice|terms of service/i);
  });

  it("starts at the sign-up page for a visitor and at the plans screen for a signed-in person", async () => {
    expect(pageHtml.match(/class="ld-plan-cta" href="\/signup"/g)).toHaveLength(3);
    expect(page).toContain("Get started");
    const signedIn = await html(true);
    expect(signedIn.match(/class="ld-plan-cta" href="\/billing\/plans"/g)).toHaveLength(3);
    expect(signedIn).toContain('href="/billing/plans"');
    expect(text(signedIn)).toContain("Choose a plan");
    expect(signedIn.match(/class="ld-plan-cta" href="\/signup"/g)).toBeNull();
  });

  it("sits inside the shared public header and footer, with our entity and mailbox", () => {
    expect(page).toContain("Who it's for");
    expect(page).toContain("© 2026 Beyrichq LTD");
    expect(pageHtml).toContain('href="mailto:privacy@beyrictech.com"');
    expect(pageHtml).not.toContain("hello@");
  });

  it("has its own metadata, a canonical, a place in the sitemap and in robots.txt, with the real prices in the description", async () => {
    expect(pricingMetadata.alternates?.canonical).toBe("/pricing");
    for (const t of [PLAN.usd, PLAN.ngn, "20 hours", "3 hours"]) expect(pricingMetadata.description).toContain(t);
    state.session = { authenticated: false };
    expect(text(renderToStaticMarkup(await PricingRoute()))).toContain("Pay when your team runs on it.");
    expect(sitemap().map((e) => e.url.replace(/^https?:\/\/[^/]+/, ""))).toContain("/pricing");
    expect(robots().rules).toEqual([expect.objectContaining({ allow: expect.arrayContaining(["/pricing"]) })]);
  });

  it("repeats the same numbers in llms.txt and in the structured data", async () => {
    const body = await llms().text();
    expect(body).not.toMatch(/to be announced|no plan is on sale/i);
    for (const t of [PLAN.usd, PLAN.ngn, PACK.usd, PACK.ngn, "20 hours", "10-hour pack", "Paystack", "Flutterwave"]) expect(body).toContain(t);
    const offers = softwareJsonLd().offers;
    expect(offers.map((o) => [o.priceCurrency, o.price])).toEqual([
      ["USD", "18"],
      ["NGN", "25000"],
    ]);
    expect(JSON.stringify(organizationJsonLd())).not.toContain("Offer");
  });
});
