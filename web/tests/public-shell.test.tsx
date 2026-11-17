import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { join } from "node:path";
import HomePage from "@/app/page";
import NotFound from "@/app/not-found";
import PricingPage from "@/app/(public)/pricing/page";
import PrivacyPage from "@/app/(public)/privacy/page";
import TermsPage from "@/app/(public)/terms/page";
import WelcomePage from "@/app/(public)/welcome/page";
import LoginPage from "@/app/login/page";
import SignupPage from "@/app/signup/page";
import ForgotPasswordPage from "@/app/forgot-password/page";
import ResetPasswordPage from "@/app/reset-password/page";
import VerifyEmailPage from "@/app/verify-email/page";
import AcceptInvitePage from "@/app/accept-invite/page";
import UnsubscribePage from "@/app/unsubscribe/page";
import PilotFullPage from "@/app/pilot-full/page";
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
  getPilot: async () => null,
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

// The markup itself, pinned. It is the landing page's own header and footer as `main` drew them before
// this change (same elements, classes and order); the only difference is that the section links start
// with the landing page's address, so they work from other pages. A quiet class or order change fails here.
const VISITOR_HEADER = "<header class=\"ld-header\"><div class=\"ld-header-in\"><a class=\"logo logo-lg\" href=\"/\"><svg width=\"30\" height=\"30\" viewBox=\"0 0 120 120\" aria-hidden=\"true\" class=\"logo-mark\"><rect width=\"120\" height=\"120\" rx=\"28\" class=\"logo-tile\"></rect><path d=\"M38 24V72A24 24 0 1 0 62 48\" class=\"logo-glyph\" stroke-width=\"14\"></path><circle cx=\"62\" cy=\"72\" r=\"6\" class=\"logo-dot\"></circle></svg><span class=\"logo-word\"><span>Beyric</span><span class=\"logo-sub\">Note Taker</span></span></a><nav class=\"ld-nav\" aria-label=\"Main\"><a href=\"/#product\">Product</a><a href=\"/#teams\">Who it&#x27;s for</a><a href=\"/pricing\">Pricing</a><a href=\"/#security\">Security</a></nav><div class=\"ld-header-actions\"><a class=\"ld-signin\" href=\"/login\">Sign in</a><a class=\"ld-get-started\" href=\"/signup\">Get started</a></div></div></header>";
const OWNER_HEADER = "<header class=\"ld-header\"><div class=\"ld-header-in\"><a class=\"logo logo-lg\" href=\"/welcome\"><svg width=\"30\" height=\"30\" viewBox=\"0 0 120 120\" aria-hidden=\"true\" class=\"logo-mark\"><rect width=\"120\" height=\"120\" rx=\"28\" class=\"logo-tile\"></rect><path d=\"M38 24V72A24 24 0 1 0 62 48\" class=\"logo-glyph\" stroke-width=\"14\"></path><circle cx=\"62\" cy=\"72\" r=\"6\" class=\"logo-dot\"></circle></svg><span class=\"logo-word\"><span>Beyric</span><span class=\"logo-sub\">Note Taker</span></span></a><nav class=\"ld-nav\" aria-label=\"Main\"><a href=\"/welcome#product\">Product</a><a href=\"/welcome#teams\">Who it&#x27;s for</a><a href=\"/pricing\">Pricing</a><a href=\"/welcome#security\">Security</a></nav><div class=\"ld-header-actions\"><a class=\"ld-get-started\" href=\"/\">Open the app</a></div></div></header>";
const VISITOR_FOOTER = "<footer class=\"ld-footer\"><div class=\"ld-footer-in\"><div class=\"ld-footer-cols\"><div class=\"ld-footer-brand\"><a class=\"logo logo-sm\" href=\"/\"><svg width=\"26\" height=\"26\" viewBox=\"0 0 120 120\" aria-hidden=\"true\" class=\"logo-mark\"><rect width=\"120\" height=\"120\" rx=\"28\" class=\"logo-tile\"></rect><path d=\"M38 24V72A24 24 0 1 0 62 48\" class=\"logo-glyph\" stroke-width=\"14\"></path><circle cx=\"62\" cy=\"72\" r=\"6\" class=\"logo-dot\"></circle></svg><span class=\"logo-word\"><span>Beyric</span><span class=\"logo-sub\">Note Taker</span></span></a><p>Meeting notes, decisions and tasks for teams, from Beyrichq LTD.</p></div><div class=\"ld-footer-col\"><span>Product</span><a href=\"/#product\">Product</a><a href=\"/#features\">Features</a><a href=\"/pricing\">Pricing</a><a href=\"/#security\">Security</a></div><div class=\"ld-footer-col\"><span>Company</span><a href=\"/privacy\">Privacy</a><a href=\"/terms\">Terms</a><a href=\"mailto:privacy@beyrictech.com\">Contact</a></div></div><div class=\"ld-footer-base\"><span>\u00a9 2026 Beyrichq LTD</span><span>notes.beyrictech.com</span></div></div></footer>";
const OWNER_FOOTER = "<footer class=\"ld-footer\"><div class=\"ld-footer-in\"><div class=\"ld-footer-cols\"><div class=\"ld-footer-brand\"><a class=\"logo logo-sm\" href=\"/welcome\"><svg width=\"26\" height=\"26\" viewBox=\"0 0 120 120\" aria-hidden=\"true\" class=\"logo-mark\"><rect width=\"120\" height=\"120\" rx=\"28\" class=\"logo-tile\"></rect><path d=\"M38 24V72A24 24 0 1 0 62 48\" class=\"logo-glyph\" stroke-width=\"14\"></path><circle cx=\"62\" cy=\"72\" r=\"6\" class=\"logo-dot\"></circle></svg><span class=\"logo-word\"><span>Beyric</span><span class=\"logo-sub\">Note Taker</span></span></a><p>Meeting notes, decisions and tasks for teams, from Beyrichq LTD.</p></div><div class=\"ld-footer-col\"><span>Product</span><a href=\"/welcome#product\">Product</a><a href=\"/welcome#features\">Features</a><a href=\"/pricing\">Pricing</a><a href=\"/welcome#security\">Security</a></div><div class=\"ld-footer-col\"><span>Company</span><a href=\"/privacy\">Privacy</a><a href=\"/terms\">Terms</a><a href=\"mailto:privacy@beyrictech.com\">Contact</a></div></div><div class=\"ld-footer-base\"><span>\u00a9 2026 Beyrichq LTD</span><span>notes.beyrictech.com</span></div></div></footer>";

const PAGES: ReadonlyArray<{ name: string; path: string; render: () => Promise<string> }> = [
  { name: "the landing page at /", path: "/", render: async () => renderPublic((await HomePage()) as ReactElement) },
  { name: "/welcome", path: "/welcome", render: () => renderInPublicLayout(WelcomePage()) },
  { name: "/pricing", path: "/pricing", render: () => renderInPublicLayout(PricingPage()) },
  { name: "/privacy", path: "/privacy", render: () => renderInPublicLayout(PrivacyPage()) },
  { name: "/terms", path: "/terms", render: () => renderInPublicLayout(TermsPage()) },
  { name: "the 404 page", path: "/no-such-page", render: () => renderPublic(NotFound() as ReactElement) },
];

// The sign-in and account screens (AuthShell) sit inside the same shell (Adebayo, 2026-11-17). They are
// for visitors: each redirects a signed-in owner, so they are checked as a visitor only.
const EMPTY_QUERY = { searchParams: Promise.resolve({}) };
const AUTH_PAGES: ReadonlyArray<{ name: string; path: string; render: () => Promise<string> }> = [
  { name: "/login", path: "/login", render: async () => renderPublic((await LoginPage(EMPTY_QUERY)) as ReactElement) },
  { name: "/signup", path: "/signup", render: async () => renderPublic((await SignupPage(EMPTY_QUERY)) as ReactElement) },
  { name: "/forgot-password", path: "/forgot-password", render: async () => renderPublic((await ForgotPasswordPage()) as ReactElement) },
  { name: "/reset-password", path: "/reset-password", render: async () => renderPublic(ResetPasswordPage() as ReactElement) },
  { name: "/verify-email", path: "/verify-email", render: async () => renderPublic(VerifyEmailPage() as ReactElement) },
  { name: "/accept-invite", path: "/accept-invite", render: async () => renderPublic(AcceptInvitePage() as ReactElement) },
  { name: "/unsubscribe", path: "/unsubscribe", render: async () => renderPublic(UnsubscribePage() as ReactElement) },
  { name: "/pilot-full", path: "/pilot-full", render: async () => renderPublic((await PilotFullPage()) as ReactElement) },
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

describe("the sign-in and account screens", () => {
  it.each(AUTH_PAGES)("$name has exactly the shared header and footer, one main landmark and no second bar", async ({ render, path }) => {
    state.pathname = path;
    const html = await render();
    const shell = await reference();
    expect(unmarked(header(html))).toBe(unmarked(header(shell)));
    expect(unmarked(footer(html))).toBe(unmarked(footer(shell)));
    expect(html.match(/<header/g)).toHaveLength(1);
    expect(html.match(/<footer/g)).toHaveLength(1);
    expect(html.match(/<main[ >]/g)).toHaveLength(1);
    expect(html).toContain('<a class="skip-link" href="#main">Skip to content</a>');
    expect(html.indexOf("skip-link")).toBeLessThan(html.indexOf("<header"));
    // The screen's own form is still there, inside the one `main`, and the old top row and footer are gone.
    const main = between(html, '<main id="main"', "</main>");
    expect(main).toContain("auth-box");
    expect(main).toMatch(/class="auth-box"><[^>]*auth-form|class="auth-box"><form/);
    expect(html).not.toMatch(/auth-bar|auth-foot/);
  });

  it("keeps the left-hand Sign in and Create account links reachable from the header", async () => {
    const html = await AUTH_PAGES[0].render();
    expect(hrefs(header(html))).toEqual(expect.arrayContaining(["/login", "/signup"]));
  });
});

describe("the pinned markup", () => {
  it("is exactly the landing page's header and footer for a visitor", async () => {
    const html = await reference();
    expect(header(html)).toBe(VISITOR_HEADER);
    expect(footer(html)).toBe(VISITOR_FOOTER);
  });

  it("is exactly the landing page's header and footer for the signed-in owner", async () => {
    state.session = owner;
    const html = await reference();
    expect(header(html)).toBe(OWNER_HEADER);
    expect(footer(html)).toBe(OWNER_FOOTER);
  });

  it("marks the current page on the same markup and changes nothing else", async () => {
    state.pathname = "/pricing";
    const html = await reference();
    expect(header(html)).toBe(VISITOR_HEADER.replace('<a href="/pricing">', '<a aria-current="page" href="/pricing">'));
    expect(footer(html)).toBe(VISITOR_FOOTER.replace('<a href="/pricing">', '<a aria-current="page" href="/pricing">'));
  });

  it("gives every page exactly one h1", async () => {
    for (const { name, path, render } of PAGES) {
      state.pathname = path;
      const html = await render();
      expect(html.match(/<h1[ >]/g), name).toHaveLength(1);
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

  it("wraps every screen drawn through AuthShell in the same shell, in one place", () => {
    expect(read(join(WEB, "components/site/auth-shell.tsx"))).toContain("<PublicShell>");
    const users = filesUnder("app", [".tsx"]).filter((file) => read(file).includes("<AuthShell"));
    expect(users.map(rel).sort()).toEqual(AUTH_PAGES.map((page) => `app${page.path}/page.tsx`).concat("app/setup/page.tsx").sort());
    for (const file of users) expect(read(file), rel(file)).not.toMatch(/PublicShell|<header|<footer/);
  });

  it("has no second header or footer left: no legal, landing or pricing variants in code or styles", () => {
    const sources = [...filesUnder("components", [".tsx", ".ts"]), ...filesUnder("app", [".tsx"]), ...filesUnder("styles", [".css"])];
    const banned = /lg-header|lg-footer|lg-tabs|ld-pfooter|LegalShell|LandingHeader|LandingFooter|PricingFooter|PricingPage/;
    expect(sources.filter((file) => banned.test(read(file))).map(rel)).toEqual([]);
    // No leftover selector of the old legal page frame either (`.lg`, `.lg > main`, `.lg #main`).
    expect(read(join(WEB, "styles/public.css"))).not.toMatch(/^\.lg[ ,{>:]/m);
    const headers = sources.filter((file) => /<header[ >]/.test(read(file)) && /public|landing|legal|pricing/.test(rel(file))).map(rel);
    expect(headers).toEqual(["components/site/public-header.tsx"]);
  });
});
