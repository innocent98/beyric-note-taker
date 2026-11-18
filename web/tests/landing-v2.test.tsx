import { describe, expect, it, vi } from "vitest";
import { Landing } from "@/components/landing/landing";
import { PublicShell } from "@/components/site/public-shell";
import { renderPublic } from "./public-render";

vi.mock("@/lib/api/server", () => ({ getSession: async () => ({ authenticated: false }) }));

// The new Landing says only what the app does (Adebayo's decisions, 2026-11-14;
// docs/superpowers/specs/2026-11-14-public-pages-audit.md). These tests keep the cuts cut.

const text = (html: string) => html.replace(/<[^>]*>/g, " ").replace(/&#x27;|&apos;/g, "'").replace(/\s+/g, " ");
// The pages inside the one public header and footer, as a visitor gets them (the shell reads the session).
const landingHtml = await renderPublic(
  <PublicShell>
    <Landing />
  </PublicShell>,
);
const landing = text(landingHtml);

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
