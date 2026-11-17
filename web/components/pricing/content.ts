// Everything the pricing page says about plans, in one file: names, prices, hours, the rows of the comparison
// table and the questions. Every number and rule here is DECIDED (Adebayo, 2026-11-14 to 2026-11-17) and is
// pinned to its source by web/tests/pricing-real.test.tsx, which fails when a price or an hour count printed here
// differs from the seed rows of the Alembic migrations (alembic/versions, first written in 0052_subscriptions.py):
//   * a one-time TRIAL of 3 hours, valid 14 days, no card (spec 3.8);
//   * ONE monthly plan, `plan_20h`, named "20 hours a month" in the seed: 20 hours a month, USD 18 or NGN 25,000 a
//     month. A person in Nigeria pays in naira through Paystack, everyone else in US dollars through Flutterwave
//     (spec 5.2: the BILLING country decides, so a public page that cannot know the visitor's country says who pays
//     in which currency instead of choosing for them);
//   * a PACK, `pack_10h`, named "10 extra hours": 10 hours, USD 10 or NGN 15,000, valid 12 months, bought only
//     with a live plan (spec 4.3);
//   * hours belong to the PERSON, not to a team (a team is only a place to share); unused plan hours do not roll
//     over (spec 3.3).
// ADDING A PLAN OR A PACK: add its seed row by migration, then add ONE entry to CATALOG below. The cards, the
// comparison table's columns, the questions, the page description, llms.txt and the JSON-LD offers are all built
// from CATALOG, and the test checks every entry against the seed, so nothing else is edited.
// The page is public and unsigned, so it cannot ask GET /api/billing/plans (that answer is for one signed-in
// person's billing country). One typed copy, one test against the seed, is the single source of truth; the SOP
// (docs/sop/2026-11-17-pricing-real-and-billing-nav.md) says what to do when a price changes.
// Left out on purpose, because the lawyer has not read them (spec 3.4 and 3.5 are flagged "the lawyer reads them
// before launch"): refund windows, what happens after a failed payment, what happens when payments stop, and
// any "we will tell you" promise, and the no-refund sentence for the unused part of a period. Two lines are
// printed that the lawyer still has to read, on Adebayo's decision of cancel-any-time (2026-11-16): the plan card's
// "Cancel any time: ..." and the FAQ "Can I cancel?" (see the SOP). No "free" wording, no "unlimited", no Business plan, no Team Plus.
// Cut from the designer's Pricing file: HubSpot sync, Jira and Asana, "Custom retention period" and "Priority
// support" (not built), "Data used to train models: Never" (not worded as our promise), the "Most teams pick this"
// badge and the seat FAQ. See docs/sop/2026-11-14-landing-v2-and-pricing.md.

export const TRIAL = { hours: 3, days: 14 } as const;

// What can be bought: monthly plans and extra-hour packs, as the seed holds them (whole dollars and whole naira:
// the seed's minor units divided by 100, 1800 cents and 2,500,000 kobo for the first plan).
export type Sellable = {
  kind: "plan" | "pack";
  // The seed code (`plans.code` or `products.code`) and the seed name, which the test compares.
  code: string;
  name: string;
  hours: number;
  usdMajor: number;
  ngnMajor: number;
  // Packs only: how long the hours stay valid (`products.valid_months`).
  validMonths?: number;
  tagline: string;
  // The list under the button, after the hours line.
  includes: ReadonlyArray<string>;
};

export const CATALOG: ReadonlyArray<Sellable> = [
  {
    kind: "plan",
    code: "plan_20h",
    name: "20 hours a month",
    hours: 20,
    usdMajor: 18,
    ngnMajor: 25_000,
    tagline: "For people whose meetings turn into work.",
    includes: [
      "Unused plan hours do not roll over to the next month",
      "Cancel any time: the plan keeps working until the period you paid for ends",
      "Extra-hour packs when you need more",
      "Every feature in the table below",
    ],
  },
  {
    kind: "pack",
    code: "pack_10h",
    name: "10 extra hours",
    hours: 10,
    usdMajor: 10,
    ngnMajor: 15_000,
    validMonths: 12,
    tagline: "For a month that runs over.",
    includes: ["Bought only with a live plan", "The hours are yours, not your team's"],
  },
];

export const usd = (major: number): string => `$${major}`;
export const ngn = (major: number): string => `₦${major.toLocaleString("en-US")}`;
export const HOURS = (n: number): string => `${n} hours`;
// "$18 or ₦25,000"
export const priceText = (s: Pick<Sellable, "usdMajor" | "ngnMajor">): string => `${usd(s.usdMajor)} or ${ngn(s.ngnMajor)}`;

const joined = (parts: string[]): string => (parts.length <= 1 ? parts.join("") : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`);

export const PLAN_ENTRIES: ReadonlyArray<Sellable> = CATALOG.filter((s) => s.kind === "plan");
export const PACK_ENTRIES: ReadonlyArray<Sellable> = CATALOG.filter((s) => s.kind === "pack");

// "20 hours for $18 or ₦25,000 a month"
const planLine = (p: Sellable): string => `${HOURS(p.hours)} for ${priceText(p)} a month`;
const packLine = (p: Sellable): string => `${HOURS(p.hours)} for ${priceText(p)}, valid ${p.validMonths} months`;

// The one sentence the page description, llms.txt and the structured data's neighbours repeat.
export const SUMMARY =
  `a one-time trial of ${HOURS(TRIAL.hours)} valid ${TRIAL.days} days, then ` +
  `${PLAN_ENTRIES.length === 1 ? "one monthly plan" : "monthly plans"}: ${joined(PLAN_ENTRIES.map(planLine))}, ` +
  `and extra-hour ${PACK_ENTRIES.length === 1 ? "pack" : "packs"}: ${joined(PACK_ENTRIES.map(packLine))}`;

// The offers the structured data publishes: every entry in both currencies.
// A monthly plan's offer carries its billing period (a crawler would otherwise read "$18" as a one-off price).
export type OfferLd = {
  "@type": "Offer";
  name: string;
  price: string;
  priceCurrency: "USD" | "NGN";
  url: string;
  priceSpecification?: { "@type": "UnitPriceSpecification"; price: string; priceCurrency: "USD" | "NGN"; billingDuration: "P1M" };
};
export function offers(siteUrl: string): ReadonlyArray<OfferLd> {
  const one = (s: Sellable, priceCurrency: "USD" | "NGN", major: number): OfferLd => ({
    "@type": "Offer",
    name: s.name,
    price: String(major),
    priceCurrency,
    url: `${siteUrl}/pricing`,
    ...(s.kind === "plan"
      ? { priceSpecification: { "@type": "UnitPriceSpecification" as const, price: String(major), priceCurrency, billingDuration: "P1M" as const } }
      : {}),
  });
  return CATALOG.flatMap((s) => [one(s, "USD", s.usdMajor), one(s, "NGN", s.ngnMajor)]);
}

export type Plan = {
  id: string;
  name: string;
  tagline: string;
  // The big line of the card ("$18", "3 hours") and what it is for.
  amount: string;
  unit: string;
  // The price in naira under the dollar one, with what it is for; null where there is no price (the trial).
  alt: string | null;
  // The first line of the list: the meeting hours.
  hoursLine: string;
  // The plan the page draws with the teal frame (the designer's middle card): the first monthly plan.
  main: boolean;
  includes: ReadonlyArray<string>;
};

const TRIAL_CARD: Plan = {
  id: "trial",
  name: "Trial",
  tagline: "For trying it on your own calls.",
  amount: HOURS(TRIAL.hours),
  unit: `one-time, valid ${TRIAL.days} days, no card`,
  alt: null,
  hoursLine: `Meeting hours: ${HOURS(TRIAL.hours)}, once, within ${TRIAL.days} days`,
  main: false,
  includes: [
    "Summary, decisions and action items",
    "Transcript with speaker names",
    "Send the note taker by link or from your calendar",
    "PDF and Word export",
    "Every other feature in the table below",
  ],
};

const cardOf = (s: Sellable, main: boolean): Plan =>
  s.kind === "plan"
    ? {
        id: s.code,
        name: s.name,
        tagline: s.tagline,
        amount: usd(s.usdMajor),
        unit: "a month, in US dollars",
        alt: `${ngn(s.ngnMajor)} a month, in naira`,
        hoursLine: `Meeting hours: ${HOURS(s.hours)} a month, for you`,
        main,
        includes: s.includes,
      }
    : {
        id: s.code,
        name: s.name,
        tagline: s.tagline,
        amount: usd(s.usdMajor),
        unit: `once, in US dollars, valid ${s.validMonths} months`,
        alt: `${ngn(s.ngnMajor)} once, in naira`,
        hoursLine: `Meeting hours: ${HOURS(s.hours)}, for you`,
        main: false,
        includes: s.includes,
      };

// The cards, in the order they are drawn: the trial, the plans, the packs.
export const PLANS: ReadonlyArray<Plan> = [
  TRIAL_CARD,
  ...PLAN_ENTRIES.map((s, i) => cardOf(s, i === 0)),
  ...PACK_ENTRIES.map((s) => cardOf(s, false)),
];

// The columns of the comparison table: the trial and every plan; a pack is an addition to a plan, not a column.
export const COMPARED: ReadonlyArray<Plan> = [TRIAL_CARD, ...PLAN_ENTRIES.map((s, i) => cardOf(s, i === 0))];

export const CURRENCY_NOTE =
  "People in Nigeria pay in naira through Paystack. Everywhere else pays in US dollars through Flutterwave. The two are separate prices, not a conversion of each other.";

export const HERO = {
  eyebrow: "PRICING",
  title: "Pay when your team runs on it.",
  body: `Notes, decisions and action items on Google Meet, Microsoft Teams and Zoom. Start with a one-time trial of ${HOURS(TRIAL.hours)}, no card. Then ${PLAN_ENTRIES.length === 1 ? "one monthly plan" : "monthly plans"}: ${joined(PLAN_ENTRIES.map(planLine))}, with extra-hour packs when you need more.`,
} as const;

export const TABLE_TITLE = "Compare";
export const TABLE_NOTE = "Outlook calendar support is new and still being tested.";

const YES = "Yes";

// One row per line of the comparison table: [label, trial, ...one cell per plan].
export function tableRows(): ReadonlyArray<ReadonlyArray<string>> {
  const each = (cell: string) => PLAN_ENTRIES.map(() => cell);
  const packs = PACK_ENTRIES.map((p) => `${p.hours} hours, ${priceText(p)}`).join("; ");
  return [
    ["Price", "No card needed", ...PLAN_ENTRIES.map((p) => `${priceText(p)} a month`)],
    ["Meeting hours", `${HOURS(TRIAL.hours)}, once`, ...PLAN_ENTRIES.map((p) => `${HOURS(p.hours)} a month`)],
    ["How long the hours last", `${TRIAL.days} days from sign-up`, ...each("The month; unused hours do not roll over")],
    ["Hours belong to the person", YES, ...each(YES)],
    [`Extra-hour packs (${packs})`, "No", ...each(YES)],
    ["Notes, decisions, action items", YES, ...each(YES)],
    ["Transcript with speaker names", YES, ...each(YES)],
    ["PDF and Word export", YES, ...each(YES)],
    ["Calendar auto-join", YES, ...each(YES)],
    ["Tasks, projects, sprints and milestones", YES, ...each(YES)],
    ["Decisions log", YES, ...each(YES)],
    ["Recurring meeting memory", YES, ...each(YES)],
    ["Ask your meetings", YES, ...each(YES)],
    ["Templates and vocabulary", YES, ...each(YES)],
    ["Slack updates", YES, ...each(YES)],
    ["Tasks sent to Linear", YES, ...each(YES)],
  ];
}

const manyPlans = PLAN_ENTRIES.length > 1;

export const FAQS: ReadonlyArray<{ q: string; a: string }> = [
  {
    q: "What does the trial include?",
    a: `Every feature in Beyric, with ${HOURS(TRIAL.hours)} of meetings to record, once. It needs no card. It ends when the ${TRIAL.hours} hours are used or ${TRIAL.days} days after you sign up, whichever comes first.`,
  },
  {
    q: `What ${manyPlans ? "do the monthly plans" : "does the monthly plan"} cost, and in which currency?`,
    a: `${joined(PLAN_ENTRIES.map((p) => `${HOURS(p.hours)} a month for ${usd(p.usdMajor)}, or ${ngn(p.ngnMajor)} in naira`))}. People in Nigeria pay in naira through Paystack; everywhere else pays in US dollars through Flutterwave.`,
  },
  {
    q: "How are meeting hours counted?",
    a: "Hours are counted in recorded meetings, and they belong to the person who has the plan, not to a team. A team is only a place to share notes. Unused plan hours do not roll over to the next month.",
  },
  {
    q: "Can my teammates read my meetings without a plan?",
    a: "Yes. They read what you share, search it and export it. They need a plan or trial of their own for Ask Beyric, translating and rewriting notes, and for their own note taker to join meetings.",
  },
  {
    q: "What if I need more hours?",
    a: `Buy a pack: ${joined(PACK_ENTRIES.map((p) => `${HOURS(p.hours)} for ${priceText(p)}, valid ${p.validMonths} months`))}. A pack can be bought only with a live plan.`,
  },
  {
    q: "What happens when my hours run out?",
    a: "A meeting that is already being recorded finishes and is counted. New recordings, including meetings your note taker would join from your calendar, wait until you add a pack, choose a plan or your plan renews. Ask Beyric, translating and rewriting notes keep working on a plan.",
  },
  {
    q: "Can I cancel?",
    a: "Yes, any time. Your plan keeps working until the end of the period you paid for, and then does not renew.",
  },
];

export const CLOSING_TITLE = "Try it on your next meeting.";
