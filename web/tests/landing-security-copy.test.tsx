import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { GET as llms } from "@/app/llms.txt/route";
import { Landing } from "@/components/landing/landing";
import { FAQS, SECURITY } from "@/components/landing/content";
import { organizationJsonLd, softwareJsonLd } from "@/components/site/json-ld";

// The public pages must say only what is true of the product (docs/sop/2026-11-14-landing-security-copy.md).
// No disk or database encryption is set up and nothing is end-to-end encrypted: only TLS in transit
// and the stored secrets (calendar tokens, pasted links, integration keys) are encrypted.
const OVERCLAIM = /AES|end[- ]to[- ]end|at rest/i;

describe("the landing security card", () => {
  it("claims encryption in transit and encrypted calendar tokens, and nothing about recordings or notes at rest", () => {
    const card = SECURITY[0];
    expect(card.title).toBe("Encrypted in transit");
    expect(card.body).toBe(
      "Traffic to the site is encrypted with TLS, and the sign-in tokens for connected calendars are stored encrypted.",
    );
  });

  it("repeats no end-to-end or at-rest claim anywhere on the page", () => {
    for (const card of SECURITY) expect(`${card.title} ${card.body}`).not.toMatch(OVERCLAIM);
    for (const faq of FAQS) expect(`${faq.q} ${faq.a}`).not.toMatch(OVERCLAIM);
    expect(renderToStaticMarkup(<Landing />)).not.toMatch(OVERCLAIM);
  });
});

describe("llms.txt and the structured data", () => {
  it("llms.txt describes a limited pilot, not 'no public sign-up', and does not call the calendar 'coming soon'", async () => {
    const text = await llms().text();
    expect(text).not.toMatch(/no public sign-up|private pilot|coming soon|free/i);
    expect(text).toContain("sign-up is invite-only for now");
    expect(text).toContain("a pilot with a limited number of accounts");
    expect(text).toContain("waiting list");
    expect(text).toContain("Connect Google Calendar so the note taker joins the meetings your rules choose by itself.");
    expect(text).not.toMatch(OVERCLAIM);
  });

  it("the JSON-LD publishes no price and no 'free', and names the legal entity", () => {
    const software = softwareJsonLd();
    expect(software).not.toHaveProperty("offers");
    expect(JSON.stringify([organizationJsonLd(), software])).not.toMatch(/private pilot|coming soon|free/i);
    expect(organizationJsonLd().name).toBe("Beyrichq LTD");
  });
});
