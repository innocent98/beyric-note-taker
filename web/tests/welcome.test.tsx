import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import HomePage, { generateMetadata as homeMetadata } from "@/app/page";
import WelcomePage, { metadata as welcomeMetadata } from "@/app/welcome/page";
import PrivacyPage from "@/app/privacy/page";
import TermsPage from "@/app/terms/page";
import { PublicShell } from "@/components/site/public-shell";
import { filesUnder, read, rel, WEB } from "./helpers";
import { join } from "node:path";

// /welcome: the landing page for everyone, and the links of the public pages that lead "home".

const session = vi.hoisted(() => ({ value: null as null | { authenticated: boolean } }));
vi.mock("@/lib/api/server", () => ({
  getSession: async () => session.value,
  getReviewOpen: async () => null,
  getTasksNew: async () => 0,
  getUnreadNotifications: async () => null,
  getShellUser: async () => null,
  getTimeZone: async () => null,
  load: <T,>(call: Promise<T>) => call,
  serverApi: async () => ({}),
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/", useRouter: () => ({ refresh: vi.fn() }) }));

const logoHref = (html: string) => /<a class="logo[^"]*" href="([^"]+)"/.exec(html)?.[1];
const hrefs = (html: string) => [...html.matchAll(/href="([^"]+)"/g)].map((m) => m[1]);
const owner = { authenticated: true };

beforeEach(() => {
  session.value = null;
});

describe("/welcome", () => {
  it("shows the landing page to a signed-in owner, with no redirect, and the same words as `/`", async () => {
    session.value = owner;
    const welcome = renderToStaticMarkup(await WelcomePage());
    session.value = null;
    const visitor = renderToStaticMarkup(await HomePage());
    expect(welcome).toContain("Your meetings, turned into finished work.");
    const body = (html: string) => html.slice(html.indexOf('<main id="main">'), html.indexOf("</main>"));
    expect(body(welcome)).toBe(body(visitor));
  });

  it("shows it to a visitor too, header unchanged", async () => {
    const html = renderToStaticMarkup(await WelcomePage());
    expect(html).toContain("Your meetings, turned into finished work.");
    expect(hrefs(html)).toEqual(expect.arrayContaining(["/login", "/signup"]));
    expect(logoHref(html)).toBe("/");
  });

  it("leads an owner's logo to /welcome and gives the header one way back into the app, not Sign in", async () => {
    session.value = owner;
    const html = renderToStaticMarkup(await WelcomePage());
    expect(logoHref(html)).toBe("/welcome");
    const header = html.slice(html.indexOf("<header"), html.indexOf("</header>"));
    expect(header).toContain("Open the app");
    expect(header).toContain('href="/"');
    expect(header).not.toContain("Sign in");
    expect(header).not.toContain("Get started");
    // The footer's logo too.
    const footer = html.slice(html.indexOf("<footer"));
    expect(/<a class="logo[^"]*" href="([^"]+)"/.exec(footer)?.[1]).toBe("/welcome");
  });

  it("leaves `/` as it was for a visitor: the landing page with the logo to `/`", async () => {
    const html = renderToStaticMarkup(await HomePage());
    expect(logoHref(html)).toBe("/");
    expect(html).toContain("Sign in");
    expect(html).not.toContain("Open the app");
    expect(html).not.toContain('href="/welcome"');
  });

  it("names `/` as its canonical, so it does not compete with it in search, and is not a second Meetings page", async () => {
    expect(welcomeMetadata.alternates?.canonical).toBe("/");
    expect(welcomeMetadata.robots).toBeUndefined(); // canonical only, no mixed signal with noindex
    const home = await homeMetadata();
    expect((home.title as { absolute: string }).absolute).toBe((welcomeMetadata.title as { absolute: string }).absolute);
    // Crawlers must be able to read /welcome to see the canonical: not blocked, not in the sitemap.
    const { default: robots } = await import("@/app/robots");
    expect(robots().rules).toEqual([expect.objectContaining({ disallow: expect.not.arrayContaining(["/welcome"]) })]);
    const { default: sitemap } = await import("@/app/sitemap");
    expect(sitemap().map((e) => e.url.replace(/^https?:\/\/[^/]+/, ""))).not.toContain("/welcome");
  });

  it("no longer redirects /welcome to `/`", () => {
    expect(read(join(WEB, "next.config.ts"))).not.toMatch(/\/welcome/);
  });

  it("is a page of its own under app/ (routes.test.ts keeps the proxy matcher equal to the list)", () => {
    expect(filesUnder("app", ["page.tsx"]).map(rel)).toContain("app/welcome/page.tsx");
  });
});

describe("the public pages' way home", () => {
  it("privacy and terms: a visitor's logo goes to `/`, an owner's to /welcome", async () => {
    for (const page of [PrivacyPage, TermsPage]) {
      session.value = null;
      const element = page() as { type: (props: unknown) => Promise<React.ReactElement>; props: unknown };
      expect(logoHref(renderToStaticMarkup(await element.type(element.props)))).toBe("/");
      session.value = owner;
      const again = page() as { type: (props: unknown) => Promise<React.ReactElement>; props: unknown };
      expect(logoHref(renderToStaticMarkup(await again.type(again.props)))).toBe("/welcome");
    }
  });

  it("the 404 page's shell: an owner's logo goes to /welcome and 'Open the app' still goes to `/`", async () => {
    session.value = owner;
    const html = renderToStaticMarkup(await PublicShell({ children: null }));
    expect(logoHref(html)).toBe("/welcome");
    expect(html).toMatch(/href="\/"[^>]*>Open the app/);
    session.value = null;
    const visitor = renderToStaticMarkup(await PublicShell({ children: null }));
    expect(logoHref(visitor)).toBe("/");
    expect(visitor).toContain("Sign in");
  });
});

describe("the sticky public headers", () => {
  const css = read(join(WEB, "styles/public.css"));
  const rule = (selector: string) => {
    const match = new RegExp(`(?:^|\\n)${selector.replace(/[.]/g, "\\.")} \\{([^}]*)\\}`).exec(css);
    expect(match, `rule for ${selector}`).not.toBeNull();
    return match![1];
  };

  it("keep the headers sticky at the top with an existing background token", () => {
    for (const selector of [".ld-header", ".lg-header"]) {
      const body = rule(selector);
      expect(body).toMatch(/position: sticky/);
      expect(body).toMatch(/top: 0/);
      expect(body).toMatch(/background: var\(--[a-z-]+\)/);
    }
  });

  it("keep them under every dialog, drawer and toast", () => {
    const z = (selector: string) => Number(/z-index: (\d+)/.exec(rule(selector))![1]);
    const components = read(join(WEB, "styles/components.css")) + read(join(WEB, "styles/pages.css"));
    const dialogs = [...components.matchAll(/z-index: (\d+)/g)].map((m) => Number(m[1])).filter((n) => n >= 40);
    expect(Math.max(z(".ld-header"), z(".lg-header"))).toBeLessThan(Math.min(...dialogs));
  });

  it("do not sit inside a scroll container: a landing wrapper with overflow-x: hidden alone stops `sticky` working", () => {
    const body = rule(".ld");
    expect(body).toMatch(/overflow-x: clip/);
    // `hidden` may stay only as the line a browser without `clip` falls back to, before it.
    expect(body.indexOf("overflow-x: hidden")).toBeLessThan(body.indexOf("overflow-x: clip"));
    for (const selector of [".lg", ".ld-header", ".lg-header"]) expect(rule(selector)).not.toMatch(/overflow(-x|-y)?: (hidden|auto|scroll)/);
  });

  it("leave the target of Skip to content and of every section link below them", () => {
    expect(rule(".ld #main")).toMatch(/scroll-margin-top: 69px/); // 68px header + 1px border
    expect(rule(".lg #main")).toMatch(/scroll-margin-top: 65px/); // 64px header + 1px border
    for (const selector of [".ld-section", ".ld-night", ".ld-faq"]) {
      expect(Number(/scroll-margin-top: (\d+)px/.exec(rule(selector))![1])).toBeGreaterThanOrEqual(69);
    }
    expect(Number(/scroll-margin-top: (\d+)px/.exec(rule(".lg-article h2"))![1])).toBeGreaterThanOrEqual(65);
  });
});
