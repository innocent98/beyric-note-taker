import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import HomePage from "@/app/page";
import PrivacyPage from "@/app/privacy/page";
import TermsPage from "@/app/terms/page";
import { PublicShell } from "@/components/site/public-shell";
import { filesUnder, read, rel, WEB } from "./helpers";
import { join } from "node:path";

// `/` is the landing page for everyone, `/meetings` is the owner's page, `/welcome` redirects to `/`; and the
// links of the public pages that lead "home".

const session = vi.hoisted(() => ({ value: null as null | { authenticated: boolean } }));
vi.mock("@/lib/api/server", () => ({
  getSession: async () => session.value,
  requireOwner: async () => {
    if (!session.value?.authenticated) throw new Error("redirect:/login");
    return {};
  },
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

describe("`/` (the landing page for everyone)", () => {
  it("shows a signed-in owner the landing page, with no redirect, and the same words as a visitor", async () => {
    session.value = owner;
    const signedIn = renderToStaticMarkup(await HomePage());
    session.value = null;
    const visitor = renderToStaticMarkup(await HomePage());
    expect(signedIn).toContain("Your meetings, turned into finished work.");
    const body = (html: string) => html.slice(html.indexOf('<main id="main">'), html.indexOf("</main>"));
    expect(body(signedIn)).toBe(body(visitor));
  });

  it("leaves a visitor's header as designed: Sign in, Get started, the logo to `/`", async () => {
    const html = renderToStaticMarkup(await HomePage());
    expect(html).toContain("Your meetings, turned into finished work.");
    expect(hrefs(html)).toEqual(expect.arrayContaining(["/login", "/signup"]));
    expect(logoHref(html)).toBe("/");
    expect(html).not.toContain("Open the app");
  });

  it("gives an owner one way back into the app, Open the app to /meetings, instead of Sign in", async () => {
    session.value = owner;
    const html = renderToStaticMarkup(await HomePage());
    expect(logoHref(html)).toBe("/");
    const header = html.slice(html.indexOf("<header"), html.indexOf("</header>"));
    expect(header).toMatch(/href="\/meetings"[^>]*>Open the app/);
    expect(header).not.toContain("Sign in");
    expect(header).not.toContain("Get started");
    const footer = html.slice(html.indexOf("<footer"));
    expect(/<a class="logo[^"]*" href="([^"]+)"/.exec(footer)?.[1]).toBe("/");
    expect(html).not.toContain('href="/welcome"');
  });

  it("is indexed with itself as canonical, for a visitor and an owner alike (no noindex)", async () => {
    for (const value of [null, owner]) {
      session.value = value;
      const home = await import("@/app/page");
      expect(home.metadata.alternates?.canonical).toBe("/");
      expect(home.metadata.robots).toBeUndefined();
    }
    const { default: robots } = await import("@/app/robots");
    expect(robots().rules).toEqual([expect.objectContaining({ allow: expect.arrayContaining(["/"]) })]);
    const { default: sitemap } = await import("@/app/sitemap");
    expect(sitemap().map((e) => e.url.replace(/^https?:\/\/[^/]+/, ""))).toContain("/");
  });
});

describe("/welcome", () => {
  it("is a permanent redirect to `/`, so old links keep working", async () => {
    const { default: config } = await import("@/next.config");
    const redirects = await config.redirects!();
    expect(redirects).toEqual([{ source: "/welcome", destination: "/", permanent: true }]);
  });

  it("is no longer a page under app/ (routes.test.ts keeps the lists equal)", () => {
    expect(filesUnder("app", ["page.tsx"]).map(rel)).not.toContain("app/welcome/page.tsx");
    expect(filesUnder("app", ["page.tsx"]).map(rel)).toContain("app/meetings/page.tsx");
  });
});

describe("/meetings (the owner's Meetings page)", () => {
  it("is never indexed and is disallowed in robots.txt", async () => {
    const { metadata } = await import("@/app/meetings/page");
    expect(metadata.robots).toEqual({ index: false, follow: false });
    const { default: robots } = await import("@/app/robots");
    expect(robots().rules).toEqual([expect.objectContaining({ disallow: expect.arrayContaining(["/meetings"]) })]);
    const { default: sitemap } = await import("@/app/sitemap");
    expect(sitemap().map((e) => e.url)).not.toEqual(expect.arrayContaining([expect.stringContaining("/meetings")]));
  });

  it("sends a visitor to /login", async () => {
    session.value = null;
    const { default: MeetingsPage } = await import("@/app/meetings/page");
    await expect(MeetingsPage()).rejects.toThrow("redirect:/login");
  });
});

describe("the public pages' way home", () => {
  it("privacy and terms: the logo goes to `/` for a visitor and for an owner", () => {
    for (const page of [PrivacyPage, TermsPage]) {
      for (const value of [null, owner]) {
        session.value = value;
        expect(logoHref(renderToStaticMarkup(page()))).toBe("/");
      }
    }
  });

  it("the 404 page's shell: the logo goes to `/` and an owner's 'Open the app' goes to /meetings", async () => {
    session.value = owner;
    const html = renderToStaticMarkup(await PublicShell({ children: null }));
    expect(logoHref(html)).toBe("/");
    expect(html).toMatch(/href="\/meetings"[^>]*>Open the app/);
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
