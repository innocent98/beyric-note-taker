// Everything the pricing page says about plans, in one file: plan names, prices, hours, the rows of the
// comparison table and the questions. NOTHING here is decided: Adebayo has not set a price or a number of
// hours for any plan (2026-11-14). When he does, the whole change is in this file:
//   * set a plan's `price` (for example "$15") and `hours` (for example "20 hours"); a `null` prints
//     TO_BE_ANNOUNCED, and the card and the comparison table both read the same two fields;
//   * edit `names` and `taglines` if the working names change.
// The structure is his decision: a one-time trial, then a paid Team plan (about 20 meeting hours a month),
// then a larger plan (about 40), the hours shared across the whole team, and extra-hour packs. Those
// numbers ("about 20", "about 40") are his guess and are deliberately NOT printed. No "free" wording, no
// "unlimited", no Business plan, and no feature is held back from a plan: every feature exists for every
// account today (docs/superpowers/specs/2026-11-14-public-pages-audit.md, section 3).
// Cut from the designer's Pricing file: HubSpot sync, Jira and Asana, "Custom retention period" and
// "Priority support" (not built), "Data used to train models: Never" (not worded as our promise), the
// "Most teams pick this" badge, per-person prices and the seat FAQ (no seat model), and the cancel,
// downgrade and discount answers (billing is not built). See docs/sop/2026-11-14-landing-v2-and-pricing.md.

export const TO_BE_ANNOUNCED = "To be announced";

export type Plan = {
  id: "trial" | "team" | "team-plus";
  name: string;
  tagline: string;
  // The price as it should read ("$15"), or null while it is not decided.
  price: string | null;
  // What the price is for ("per month"), shown under it.
  priceUnit: string;
  // Meeting hours ("20 hours"), or null while it is not decided.
  hours: string | null;
  // How the hours are given: once, or each month and shared by the team.
  hoursUnit: string;
  // The card's button. Every plan starts at the sign-up page, because no paid plan is on sale yet.
  cta: string;
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
    price: null,
    priceUnit: "one-time trial",
    hours: null,
    hoursUnit: "one time",
    cta: "Get started",
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
    id: "team",
    name: "Team",
    tagline: "For teams that want meetings to turn into work.",
    price: null,
    priceUnit: "per month",
    hours: null,
    hoursUnit: "each month, shared by the whole team",
    cta: "Start with the trial",
    main: true,
    includes: ["Extra-hour packs when the team needs more", "Every feature in the table below"],
  },
  {
    id: "team-plus",
    name: "Team Plus",
    tagline: "For teams that meet more.",
    price: null,
    priceUnit: "per month",
    hours: null,
    hoursUnit: "each month, shared by the whole team",
    cta: "Start with the trial",
    main: false,
    includes: ["Extra-hour packs when the team needs more", "Every feature in the table below"],
  },
];

export const priceOf = (plan: Plan): string => plan.price ?? TO_BE_ANNOUNCED;
export const hoursOf = (plan: Plan): string => plan.hours ?? TO_BE_ANNOUNCED;
export const hoursLine = (plan: Plan): string => `Meeting hours: ${hoursOf(plan)}, ${plan.hoursUnit}`;

export const HERO = {
  eyebrow: "PRICING",
  title: "Pay when your team runs on it.",
  body: "Every plan includes notes, decisions and action items on Google Meet, Microsoft Teams and Zoom. No plan is on sale yet, and every price and number of hours below is still to be announced.",
} as const;

export const TABLE_TITLE = "Compare plans";
export const TABLE_NOTE = "Outlook calendar support is new and still being tested.";

const YES = "Yes";
const ALL_YES: [string, string, string] = [YES, YES, YES];

// One row per line of the comparison table: [label, trial, team, team plus].
export function tableRows(plans: ReadonlyArray<Plan>): ReadonlyArray<readonly [string, string, string, string]> {
  const [trial, team, plus] = plans;
  return [
    ["Meeting hours", `${hoursOf(trial)}, ${trial.hoursUnit}`, `${hoursOf(team)} a month`, `${hoursOf(plus)} a month`],
    ["Hours shared across the team", TO_BE_ANNOUNCED, YES, YES],
    ["Extra-hour packs", TO_BE_ANNOUNCED, YES, YES],
    ["Notes, decisions, action items", ...ALL_YES],
    ["Transcript with speaker names", ...ALL_YES],
    ["PDF and Word export", ...ALL_YES],
    ["Calendar auto-join", ...ALL_YES],
    ["Tasks, projects, sprints and milestones", ...ALL_YES],
    ["Decisions log", ...ALL_YES],
    ["Recurring meeting memory", ...ALL_YES],
    ["Ask your meetings", ...ALL_YES],
    ["Templates and vocabulary", ...ALL_YES],
    ["Slack updates", ...ALL_YES],
    ["Tasks sent to Linear", ...ALL_YES],
  ];
}

export const FAQS: ReadonlyArray<{ q: string; a: string }> = [
  {
    q: "Can I buy a plan today?",
    a: "Not yet. Beyric is invite-only for now and no paid plan is on sale. This page shows the plans we expect to offer. Every price and number of hours is marked to be announced until it is decided.",
  },
  {
    q: "How are meeting hours counted?",
    a: "Plans are counted in hours of recorded meetings, not in seats. On a paid plan the hours are shared by the whole team, and extra-hour packs can add more. How many hours each plan includes is to be announced.",
  },
  {
    q: "What does the trial include?",
    a: "Every feature in Beyric. What is limited is the number of meeting hours, which is to be announced. The trial's hours are given once, not each month.",
  },
];

export const CLOSING_TITLE = "Try it on your next meeting.";
