import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { join } from "node:path";
import { GET as llms } from "@/app/llms.txt/route";
import sitemap from "@/app/sitemap";
import robots from "@/app/robots";
import PricingRoute, { metadata as pricingMetadata } from "@/app/(public)/pricing/page";
import { Landing } from "@/components/landing/landing";
import { Pricing } from "@/components/pricing/pricing";
import { PublicShell } from "@/components/site/public-shell";
import { renderPublic } from "./public-render";
import { PLANS, TO_BE_ANNOUNCED, hoursLine, priceOf, tableRows, type Plan } from "@/components/pricing/content";
import { read, WEB } from "./helpers";

vi.mock("@/lib/api/server", () => ({ getSession: async () => ({ authenticated: false }) }));

// The new Landing and the Pricing page say only what the app does (Adebayo's decisions, 2026-11-14;
// docs/superpowers/specs/2026-11-14-public-pages-audit.md). These tests keep the cuts cut.

const text = (html: string) => html.replace(/<[^>]*>/g, " ").replace(/&#x27;|&apos;/g, "'").replace(/\s+/g, " ");
// The pages inside the one public header and footer, as a visitor gets them (the shell reads the session).
const landingHtml = await renderPublic(
  <PublicShell>
    <Landing />
  </PublicShell>,
);
const pricingHtml = await renderPublic(
  <PublicShell>
    <Pricing />
  </PublicShell>,
);
const landing = text(landingHtml);
const pricing = text(pricingHtml);

describe("the landing page: what is cut", () => {
  it.each([
    ["CRM and HubSpot", /hubspot|\bCRM\b/i],
    ["Jira or Asana as something it works with", /jira|asana/i],
    ["times in the transcript", /\b\d{2}:\d{2}\b(?! *[a-z]*day)|who said what and when/i],
    ["a brief to every attendee", /every attendee/i],
    ["unlimited anything", /unlimited/i],
    ["the hiring card", /hiring teams/i],
    ["encryption at rest or end to end", /AES|end[- ]to[- ]end|at rest/i],
    ["a promise about training", /never train|never used for training|nothing to opt out/i],
    ["a clear name", /\ba clear name/i],
    ["free wording", /\bfree\b/i],
    ["a claim about other note takers", /typical note taker|other (ai )?note takers|most note takers/i],
  ])("has no %s", (_what, pattern) => {
    // "Next: Mon 9 Nov, 14:00" is a meeting time in a sample, not a transcript time: allow that one.
    // The FAQ says Jira and Asana are not supported: that sentence is the one place they are named.
    expect(landing.replace("Mon 9 Nov, 14:00", "").replace("Jira and Asana are not supported", "")).not.toMatch(pattern);
  });

  it("says plainly that sign-up is invite-only, with the way in for everyone else", () => {
    expect(landing).toContain("Sign-up is invite-only for now.");
    expect(landing).toContain("join the waiting list");
  });

  it("keeps our entity and mailbox in the footer", () => {
    expect(landing).toContain("© 2026 Beyrichq LTD");
    expect(landing).toContain("from Beyrichq LTD");
    expect(landing).not.toContain("Beyric Technologies");
    expect(landingHtml).toContain('href="mailto:privacy@beyrictech.com"');
    expect(landingHtml).not.toContain("hello@");
  });

  it("words the bot's name, Outlook, Linear and translation as the app has them", () => {
    expect(landing).toContain("under a name you can see");
    expect(landing).toContain("Outlook (new, and still being tested)");
    expect(landing).toContain("Jira and Asana are not supported");
    expect(landing).toContain("French or Spanish");
  });

  it("links Pricing from the header, the footer and the closing band", () => {
    expect(landingHtml.match(/href="\/pricing"/g)?.length).toBeGreaterThanOrEqual(3);
  });
});

describe("the pricing page", () => {
  it("marks every price and number of hours 'to be announced', and shows no figure", () => {
    expect(pricing).toContain(TO_BE_ANNOUNCED);
    expect(pricing).not.toMatch(/[$€£₦]\s?\d|\b\d+\s?(hours?|h)\b|\$00/i);
    for (const plan of PLANS) {
      expect(priceOf(plan)).toBe(TO_BE_ANNOUNCED);
      expect(hoursLine(plan)).toContain(TO_BE_ANNOUNCED);
    }
  });

  it("has a one-time trial, a Team plan and a larger plan, and no Business plan, no free and no unlimited", () => {
    expect(PLANS.map((p) => p.name)).toEqual(["Trial", "Team", "Team Plus"]);
    expect(pricing).not.toMatch(/business|\bfree\b|unlimited|forever|hubspot|jira|asana|most teams pick/i);
    expect(pricing).toContain("Extra-hour packs");
  });

  it("says the hours belong to the person, never that a team shares them (decided 2026-11-16)", () => {
    expect(pricing).toContain("Hours belong to the person");
    expect(pricing).toContain("the hours belong to the person who has the plan, not to a team");
    expect(pricing).not.toMatch(/whole team|shared by|shared across|share[sd]? (the )?hours|pooled/i);
    const description = pricingMetadata.description ?? "";
    expect(description).toContain("belong to the person");
    expect(description).not.toMatch(/shared|whole team/i);
  });

  it("gates no feature: every feature row is 'Yes' on every plan", () => {
    const features = tableRows(PLANS).slice(3);
    expect(features.length).toBeGreaterThan(8);
    for (const [label, ...cells] of features) expect(cells, label).toEqual(["Yes", "Yes", "Yes"]);
    const labels = tableRows(PLANS).map((r) => r[0]);
    expect(labels).toEqual(expect.arrayContaining(["Calendar auto-join", "Tasks, projects, sprints and milestones", "Templates and vocabulary"]));
  });

  it("has every plan name, price, hour value and row in one data file: a decided number drops in with one edit", () => {
    const decided: Plan[] = PLANS.map((p) => (p.id === "team" ? { ...p, price: "$15", hours: "20 hours" } : p));
    expect(priceOf(decided[1])).toBe("$15");
    expect(tableRows(decided)[0][2]).toBe("20 hours a month");
    expect(hoursLine(decided[1])).toContain("20 hours");
    const component = read(join(WEB, "components/pricing/pricing.tsx"));
    for (const word of ["Team Plus", "Trial", "To be announced", "Extra-hour", "Calendar auto-join"]) expect(component).not.toContain(word);
  });

  it("starts every plan at the sign-up page, since no plan is on sale", () => {
    expect(pricingHtml.match(/class="ld-plan-cta" href="\/signup"/g)).toHaveLength(3);
    expect(pricing).toContain("No plan is on sale yet");
  });

  it("sits inside the shared public header and footer, with our entity and mailbox", () => {
    expect(pricing).toContain("Who it's for");
    expect(pricing).toContain("© 2026 Beyrichq LTD");
    expect(pricingHtml).toContain('href="mailto:privacy@beyrictech.com"');
    expect(pricingHtml).not.toContain("hello@");
    expect(pricingHtml).not.toContain("ld-pfooter");
  });

  it("is a public page with its own metadata and canonical, in the sitemap and allowed in robots.txt", async () => {
    expect(pricingMetadata.alternates?.canonical).toBe("/pricing");
    expect(text(renderToStaticMarkup(PricingRoute()))).toContain("Pay when your team runs on it.");
    expect(sitemap().map((e) => e.url.replace(/^https?:\/\/[^/]+/, ""))).toContain("/pricing");
    expect(robots().rules).toEqual([expect.objectContaining({ allow: expect.arrayContaining(["/pricing"]) })]);
  });

  it("is in llms.txt without a price", async () => {
    const body = await llms().text();
    expect(body).toContain("/pricing");
    expect(body).toContain("still to be announced");
  });
});
