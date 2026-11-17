import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { join } from "node:path";
import HomePage from "@/app/page";
import NotFound from "@/app/not-found";
import PricingPage from "@/app/(public)/pricing/page";
import PrivacyPage from "@/app/(public)/privacy/page";
import TermsPage from "@/app/(public)/terms/page";
import WelcomePage from "@/app/(public)/welcome/page";
import PublicLayout from "@/app/(public)/layout";
import { PublicShell } from "@/components/site/public-shell";
import { filesUnder, read, rel, WEB } from "./helpers";
import { between, footer, header, hrefs, renderInPublicLayout, renderPublic } from "./public-render";

// Every public page wears the same header and the same footer (Adebayo's decision, 2026-11-17): the
// landing page at `/` and /welcome, /pricing, /privacy, /terms and the 404 page. These tests render each
// page the way Next does (its route-group layout, or its own wrapper) and compare what comes out.

const state = vi.hoisted(() => ({ session: null as null | { authenticated: boolean }, pathname: "/" }));
vi.mock("@/lib/api/server", () => ({
  getSession: async () => state.session,
  getReviewOpen: async () => null,
  getTasksNew: async () => 0,
  getUnreadNotifications: async () => null,
  getShellUser: async () => null,
  getShellTeams: async () => null,
  getTimeZone: async () => null,
  load: <T,>(call: Promise<T>) => call,
  serverApi: async () => ({}),
}));
vi.mock("next/navigation", () => ({ usePathname: () => state.pathname, useRouter: () => ({ refresh: vi.fn() }) }));

const owner = { authenticated: true };

const PAGES: ReadonlyArray<{ name: string; path: string; render: () => Promise<string> }> = [
  { name: "the landing page at /", path: "/", render: async () => renderPublic((await HomePage()) as ReactElement) },
  { name: "/welcome", path: "/welcome", render: () => renderInPublicLayout(WelcomePage()) },
  { name: "/pricing", path: "/pricing", render: () => renderInPublicLayout(PricingPage()) },
  { name: "/privacy", path: "/privacy", render: () => renderInPublicLayout(PrivacyPage()) },
  { name: "/terms", path: "/terms", render: () => renderInPublicLayout(TermsPage()) },
  { name: "the 404 page", path: "/no-such-page", render: () => renderPublic(NotFound() as ReactElement) },
];

// The shell alone, for the same visitor: what every page's header and footer must equal.
const reference = async () => renderPublic(<PublicShell>{null}</PublicShell>);
const unmarked = (html: string) => html.replaceAll(' aria-current="page"', "");

beforeEach(() => {
  state.session = null;
  state.pathname = "/";
});

describe("the public header and footer", () => {
  it.each(PAGES)("$name has exactly the shared header and footer (a visitor)", async ({ render, path }) => {
    state.pathname = path;
    const html = await render();
    const shell = await reference();
    expect(unmarked(header(html))).toBe(unmarked(header(shell)));
    expect(unmarked(footer(html))).toBe(unmarked(footer(shell)));
    // One of each landmark, with the skip link first and its target.
    expect(html.match(/<header/g)).toHaveLength(1);
    expect(html.match(/<footer/g)).toHaveLength(1);
    expect(html.match(/<main[ >]/g)).toHaveLength(1);
    expect(html.match(/<nav[ >]/g)).toHaveLength(1);
    expect(html).toContain('<a class="skip-link" href="#main">Skip to content</a>');
    expect(html).toContain('<main id="main"');
    expect(html.indexOf("skip-link")).toBeLessThan(html.indexOf("<header"));
  });

  // `/` is the owner's Meetings page, not a public page; the owner reads the landing page at /welcome.
  it.each(PAGES.filter((page) => page.path !== "/"))("$name has exactly the shared header and footer (the signed-in owner)", async ({ render, path }) => {
    state.session = owner;
    state.pathname = path;
    const html = await render();
    const shell = await reference();
    expect(unmarked(header(html))).toBe(unmarked(header(shell)));
    expect(unmarked(footer(html))).toBe(unmarked(footer(shell)));
    expect(header(html)).toContain("Open the app");
    expect(header(html)).not.toContain("Sign in");
    expect(header(html)).not.toContain("Get started");
  });

  it("draws the designed header: logo, four links, Sign in and Get started for a visitor", async () => {
    const html = header(await reference());
    expect(hrefs(html)).toEqual(["/", "/#product", "/#teams", "/pricing", "/#security", "/login", "/signup"]);
    expect(html.replace(/<[^>]*>/g, " ").replaceAll("&#x27;", "'").replace(/\s+/g, " ")).toContain("Product Who it's for Pricing Security Sign in Get started");
    expect(html).toContain('aria-label="Main"');
  });

  it("sends an owner's header to the landing page at /welcome, and 'Open the app' to `/`", async () => {
    state.session = owner;
    const html = header(await reference());
    expect(hrefs(html)).toEqual(["/welcome", "/welcome#product", "/welcome#teams", "/pricing", "/welcome#security", "/"]);
    expect(html).toContain("Open the app");
    expect(/<a class="logo[^"]*" href="([^"]+)"/.exec(footer(await reference()))?.[1]).toBe("/welcome");
  });

  it("draws the full footer on every page: columns, legal links, Contact, entity and copyright", async () => {
    const html = footer(await reference());
    expect(hrefs(html)).toEqual([
      "/",
      "/#product",
      "/#features",
      "/pricing",
      "/#security",
      "/privacy",
      "/terms",
      "mailto:privacy@beyrictech.com",
    ]);
    const text = html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
    expect(text).toContain("© 2026 Beyrichq LTD");
    expect(text).toContain("Meeting notes, decisions and tasks for teams, from Beyrichq LTD.");
  });

  it("still offers Sign in when FastAPI cannot be reached (no session)", async () => {
    state.session = null;
    for (const { render } of PAGES) {
      const html = header(await render());
      expect(html).toContain("Sign in");
      expect(html).not.toContain("Open the app");
    }
  });

  it("leads every section link to the landing page, so each works from every page", async () => {
    for (const { render } of PAGES) {
      const links = hrefs(header(await render()));
      for (const link of links.filter((h) => h.includes("#"))) expect(link.startsWith("/#")).toBe(true);
    }
  });

  it("marks the current page: Pricing on /pricing, Privacy on /privacy, Terms on /terms, nothing on the landing page", async () => {
    const current = (html: string) => [...html.matchAll(/<a [^>]*aria-current="page"[^>]*>(.*?)<\/a>/g)].map((m) => m[1].replace(/<[^>]*>/g, ""));
    for (const [path, header_, footer_] of [
      ["/pricing", ["Pricing"], ["Pricing"]],
      ["/privacy", [], ["Privacy"]],
      ["/terms", [], ["Terms"]],
      ["/", [], []],
      ["/welcome", [], []],
      ["/no-such-page", [], []],
    ] as const) {
      state.pathname = path;
      const html = await reference();
      expect(current(header(html)), `header on ${path}`).toEqual(header_);
      expect(current(footer(html)), `footer on ${path}`).toEqual(footer_);
    }
  });
});

describe("what each page keeps of its own", () => {
  it("keeps the legal pages' 'On this page' list and their text inside the shared main landmark", async () => {
    for (const page of [PrivacyPage, TermsPage]) {
      const html = await renderInPublicLayout(page());
      const main = between(html, '<main id="main"', "</main>");
      expect(main).toContain('aria-label="On this page"');
      expect(main).toContain("<article");
      expect(main).toContain("privacy@beyrictech.com");
    }
  });

  it("keeps the 404 page's own words and its way out", async () => {
    const html = await renderPublic(NotFound() as ReactElement);
    expect(html).toContain("Page not found");
    expect(html).toContain("Go to the home page");
  });
});

describe("one implementation, wired once", () => {
  it("wraps every page of the (public) route group in the shell, with no page-by-page wiring", async () => {
    const pages = filesUnder("app/(public)", ["page.tsx"]).map(rel).sort();
    expect(pages).toEqual(["app/(public)/pricing/page.tsx", "app/(public)/privacy/page.tsx", "app/(public)/terms/page.tsx", "app/(public)/welcome/page.tsx"]);
    const html = await renderPublic(PublicLayout({ children: <p>a page added later</p> }) as ReactElement);
    expect(html).toContain("a page added later");
    expect(html).toContain("ld-header");
    expect(html).toContain("ld-footer");
    for (const file of pages) expect(read(join(WEB, file)), file).not.toMatch(/PublicShell|<header|<footer/);
  });

  it("wraps the two public pages that cannot be in the group (`/` and the 404 page) in the same shell", () => {
    for (const file of ["app/page.tsx", "app/not-found.tsx"]) expect(read(join(WEB, file)), file).toContain("<PublicShell>");
  });

  it("has no second header or footer left: no legal, landing or pricing variants in code or styles", () => {
    const sources = [...filesUnder("components", [".tsx", ".ts"]), ...filesUnder("app", [".tsx"]), ...filesUnder("styles", [".css"])];
    const banned = /lg-header|lg-footer|lg-tabs|ld-pfooter|LegalShell|LandingHeader|LandingFooter|PricingFooter|PricingPage/;
    expect(sources.filter((file) => banned.test(read(file))).map(rel)).toEqual([]);
    const headers = sources.filter((file) => /<header[ >]/.test(read(file)) && /public|landing|legal|pricing/.test(rel(file))).map(rel);
    expect(headers).toEqual(["components/site/public-header.tsx"]);
  });
});
