import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { AppShell } from "@/components/app/shell";
import { NEXT_PAGES } from "@/lib/routes";
import { config } from "@/proxy";
import type { TeamList } from "@/lib/api/types";
import { teamsBody } from "./helpers";

// The Recipes entry of the sidebar (lead-designed: the designer's sidebar has no such link) and the page behind it.
vi.mock("next/navigation", () => ({
  usePathname: () => "/recipes",
  useSearchParams: () => new URLSearchParams(""),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
}));
const teams = teamsBody<TeamList>("46-teams-list.json").teams;

describe("the Recipes link", () => {
  it("is in the Configure group, after Templates, and marks the page it leads to", () => {
    const html = renderToStaticMarkup(<AppShell current="recipes">x</AppShell>);
    expect(html).toContain('href="/recipes"');
    const configure = html.slice(html.indexOf('aria-label="Configure"'));
    expect(configure.indexOf("Templates")).toBeLessThan(configure.indexOf("Recipes"));
    expect(configure.indexOf("Recipes")).toBeLessThan(configure.indexOf("Vocabulary"));
    const anchor = html.match(/<a[^>]*href="\/recipes"[^>]*>/)?.[0] ?? "";
    expect(anchor).toContain('aria-current="page"');
  });

  it("follows the team when the person is on several: the link is the team-aware one", () => {
    const html = renderToStaticMarkup(
      <AppShell current="recipes" teams={teams}>
        x
      </AppShell>,
    );
    expect(html).toContain('href="/recipes"'); // the default team: the plain address
  });

  it("is a page Next renders itself, so it gets the nonce policy", () => {
    expect(NEXT_PAGES).toContain("/recipes");
    expect(config.matcher).toContain("/recipes");
  });
});

// The Documents entry (lead-designed, beside Recipes) and its page.
describe("the Documents link", () => {
  it("is in the Configure group, right after Recipes, and marks the page it leads to", () => {
    const html = renderToStaticMarkup(<AppShell current="documents">x</AppShell>);
    const configure = html.slice(html.indexOf('aria-label="Configure"'));
    expect(configure.indexOf("Recipes")).toBeLessThan(configure.indexOf("Documents"));
    expect(configure.indexOf("Documents")).toBeLessThan(configure.indexOf("Vocabulary"));
    const anchor = html.match(/<a[^>]*href="\/documents"[^>]*>/)?.[0] ?? "";
    expect(anchor).toContain('aria-current="page"');
  });

  it("is not marked on another page, and follows the team like Recipes (the plain address for the default team)", () => {
    const html = renderToStaticMarkup(
      <AppShell current="recipes" teams={teams}>
        x
      </AppShell>,
    );
    const anchor = html.match(/<a[^>]*href="\/documents"[^>]*>/)?.[0] ?? "";
    expect(anchor).not.toContain("aria-current");
  });

  it("is a page Next renders itself, so it gets the nonce policy", () => {
    expect(NEXT_PAGES).toContain("/documents");
    expect(config.matcher).toContain("/documents");
  });
});
