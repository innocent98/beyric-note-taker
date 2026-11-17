// Everything the pricing page says about plans, in one file: names, prices, hours, the rows of the comparison
// table and the questions. Every number and rule here is DECIDED (Adebayo, 2026-11-14 to 2026-11-17) and is
// pinned to its source by web/tests/pricing-real.test.tsx, which fails when a price or an hour count printed here
// differs from the seed of alembic/versions/0052_subscriptions.py:
//   * a one-time TRIAL of 3 hours, valid 14 days, no card (spec 3.8);
//   * ONE monthly plan, `plan_20h`, named "20 hours a month" in the seed: 20 hours a month, USD 18 or NGN 25,000 a
//     month. A person in Nigeria pays in naira through Paystack, everyone else in US dollars through Flutterwave
//     (spec 5.2: the BILLING country decides, so a public page that cannot know the visitor's country says who pays
//     in which currency instead of choosing for them);
//   * a PACK, `pack_10h`, named "10 extra hours": 10 hours, USD 10 or NGN 15,000, valid 12 months, bought only
//     with a live plan (spec 4.3);
//   * hours belong to the PERSON, not to a team (a team is only a place to share); unused plan hours do not roll
//     over (spec 3.3).
// The page is public and unsigned, so it cannot ask GET /api/billing/plans (that answer is for one signed-in
// person's billing country). One typed copy, one test against the seed, is the single source of truth; the SOP
// (docs/sop/2026-11-17-pricing-real-and-billing-nav.md) says what to do when a price changes.
// Left out on purpose, because the lawyer has not read them (spec 3.4 and 3.5 are flagged "the lawyer reads them
// before launch"): refund windows, what happens after a failed payment, what happens when payments stop, and
// any "we will tell you" promise. No "free" wording, no "unlimited", no Business plan, no Team Plus.
// Cut from the designer's Pricing file: HubSpot sync, Jira and Asana, "Custom retention period" and "Priority
// support" (not built), "Data used to train models: Never" (not worded as our promise), the "Most teams pick this"
// badge and the seat FAQ. See docs/sop/2026-11-14-landing-v2-and-pricing.md.

// The numbers, as the seed holds them: one place, read by the cards, the table, the questions and the tests.
export const TRIAL = { hours: 3, days: 14 } as const;
// Whole dollars and whole naira, as the seed's minor units divided by 100 (1800 cents, 2,500,000 kobo).
const usd = (major: number): string => `$${major}`;
const ngn = (major: number): string => `₦${major.toLocaleString("en-US")}`;

export const PLAN = {
  code: "plan_20h",
  name: "20 hours a month",
  hours: 20,
  usdMajor: 18,
  ngnMajor: 25_000,
  usd: usd(18),
  ngn: ngn(25_000),
} as const;
export const PACK = {
  code: "pack_10h",
  name: "10 extra hours",
  hours: 10,
  months: 12,
  usdMajor: 10,
  ngnMajor: 15_000,
  usd: usd(10),
  ngn: ngn(15_000),
} as const;

export const HOURS = (n: number): string => `${n} hours`;

export type Plan = {
  id: "trial" | "plan" | "pack";
  name: string;
  tagline: string;
  // The big line of the card ("$18", "3 hours") and what it is for.
  amount: string;
  unit: string;
  // The price in naira under the dollar one, with what it is for; null where there is no price (the trial).
  alt: string | null;
  // The first line of the list: the meeting hours.
  hoursLine: string;
  // The plan the page draws with the teal frame (the designer's middle card).
  main: boolean;
  // The list under the button, after the hours line.
  includes: ReadonlyArray<string>;
};

export const PLANS: ReadonlyArray<Plan> = [
  {
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
  },
  {
    id: "plan",
    name: PLAN.name,
    tagline: "For people whose meetings turn into work.",
    amount: PLAN.usd,
    unit: "a month, in US dollars",
    alt: `${PLAN.ngn} a month, in naira`,
    hoursLine: `Meeting hours: ${HOURS(PLAN.hours)} a month, for you`,
    main: true,
    includes: [
      "Unused plan hours do not roll over to the next month",
      "Cancel any time: the plan keeps working until the period you paid for ends",
      "Extra-hour packs when you need more",
      "Every feature in the table below",
    ],
  },
  {
    id: "pack",
    name: PACK.name,
    tagline: "For a month that runs over.",
    amount: PACK.usd,
    unit: `once, in US dollars, valid ${PACK.months} months`,
    alt: `${PACK.ngn} once, in naira`,
    hoursLine: `Meeting hours: ${HOURS(PACK.hours)}, for you`,
    main: false,
    includes: ["Bought only with a live plan", "The hours are yours, not your team's"],
  },
];

// The two plans the comparison table sets side by side; the pack is an addition to a plan, not a third column.
export const COMPARED: ReadonlyArray<Plan> = PLANS.filter((p) => p.id !== "pack");

export const CURRENCY_NOTE =
  "People in Nigeria pay in naira through Paystack. Everywhere else pays in US dollars through Flutterwave. The two are separate prices, not a conversion of each other.";

export const HERO = {
  eyebrow: "PRICING",
  title: "Pay when your team runs on it.",
  body: `Notes, decisions and action items on Google Meet, Microsoft Teams and Zoom. Start with a one-time trial of ${HOURS(TRIAL.hours)}, no card. Then one monthly plan of ${HOURS(PLAN.hours)}: ${PLAN.usd} or ${PLAN.ngn} a month, with ${PACK.hours}-hour packs when you need more.`,
} as const;

export const TABLE_TITLE = "Compare";
export const TABLE_NOTE = "Outlook calendar support is new and still being tested.";

const YES = "Yes";

// One row per line of the comparison table: [label, trial, plan].
export function tableRows(): ReadonlyArray<readonly [string, string, string]> {
  return [
    ["Price", "No card needed", `${PLAN.usd} or ${PLAN.ngn} a month`],
    ["Meeting hours", `${HOURS(TRIAL.hours)}, once`, `${HOURS(PLAN.hours)} a month`],
    ["How long the hours last", `${TRIAL.days} days from sign-up`, "The month; unused hours do not roll over"],
    ["Hours belong to the person", YES, YES],
    [`Extra-hour packs (${PACK.hours} hours, ${PACK.usd} or ${PACK.ngn})`, "No", "Yes"],
    ["Notes, decisions, action items", YES, YES],
    ["Transcript with speaker names", YES, YES],
    ["PDF and Word export", YES, YES],
    ["Calendar auto-join", YES, YES],
    ["Tasks, projects, sprints and milestones", YES, YES],
    ["Decisions log", YES, YES],
    ["Recurring meeting memory", YES, YES],
    ["Ask your meetings", YES, YES],
    ["Templates and vocabulary", YES, YES],
    ["Slack updates", YES, YES],
    ["Tasks sent to Linear", YES, YES],
  ];
}

export const FAQS: ReadonlyArray<{ q: string; a: string }> = [
  {
    q: "What does the trial include?",
    a: `Every feature in Beyric, with ${HOURS(TRIAL.hours)} of meetings to record, once. It needs no card. It ends when the ${TRIAL.hours} hours are used or ${TRIAL.days} days after you sign up, whichever comes first.`,
  },
  {
    q: "What does the monthly plan cost, and in which currency?",
    a: `${PLAN.usd} a month, or ${PLAN.ngn} a month in naira. People in Nigeria pay in naira through Paystack; everywhere else pays in US dollars through Flutterwave. It includes ${HOURS(PLAN.hours)} of meetings each month.`,
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
    a: `Buy a pack: ${PACK.hours} hours for ${PACK.usd} or ${PACK.ngn}, valid ${PACK.months} months. A pack can be bought only with a live plan.`,
  },
  {
    q: "What happens when my hours run out?",
    a: "A meeting that is already being recorded finishes and is counted. New recordings, including meetings your note taker would join from your calendar, wait until you add a pack or your plan renews. Ask Beyric, translating and rewriting notes keep working on a plan.",
  },
  {
    q: "Can I cancel?",
    a: "Yes, any time. Your plan keeps working until the end of the period you paid for, and then does not renew. The unused part of the period is not refunded.",
  },
];

export const CLOSING_TITLE = "Try it on your next meeting.";
