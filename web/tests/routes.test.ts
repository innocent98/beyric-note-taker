import { describe, expect, it } from "vitest";
import { NEXT_PAGES, NEXT_ROUTES, isNextPage } from "@/lib/routes";
import { config } from "@/proxy";
import { filesUnder, rel } from "./helpers";

describe("routes", () => {
  const pathOf = (file: string, leaf: string) =>
    "/" + rel(file).replace(/^app\//, "").replace(new RegExp(`/?${leaf}$`), "");
  const pagePaths = filesUnder("app", ["page.tsx"]).map((p) => pathOf(p, "page\\.tsx")).sort();

  it("lists exactly the pages under app/ in NEXT_PAGES", () => {
    expect([...NEXT_PAGES].sort()).toEqual(pagePaths);
  });

  it("lists exactly the route handlers under app/ (besides healthz and llms.txt) in NEXT_ROUTES", () => {
    const handlers = filesUnder("app", ["route.ts"])
      .map((p) => pathOf(p, "route\\.ts"))
      .filter((p) => p !== "/healthz" && p !== "/llms.txt")
      .sort();
    expect([...NEXT_ROUTES].sort()).toEqual(handlers);
  });

  it("has a proxy matcher equal to NEXT_PAGES, so only Next-rendered pages get the nonce CSP", () => {
    const asMatcher = [...NEXT_PAGES].map((p) => p.replace(/\[(\w+)\]/g, ":$1")).sort();
    expect([...config.matcher].sort()).toEqual(asMatcher);
  });

  it("recognises Next pages with a query or hash, and nothing else", () => {
    expect(isNextPage("/privacy#google")).toBe(true);
    expect(isNextPage("/terms?x=1")).toBe(true);
    expect(isNextPage("/")).toBe(true);
    expect(isNextPage("/login")).toBe(true);
    expect(isNextPage("/meetings/12")).toBe(true);
    expect(isNextPage("/meetings/12?x=1")).toBe(true);
    expect(isNextPage("/series/12")).toBe(true);
    expect(isNextPage("/series/12/agenda")).toBe(false);
    expect(isNextPage("/meetings/12/cancel")).toBe(false);
    expect(isNextPage("/meetings")).toBe(true);
    expect(isNextPage("/welcome")).toBe(false); // a redirect to `/` in next.config.ts, not a page
    expect(isNextPage("/privacy/extra")).toBe(false);
    expect(isNextPage("/logout")).toBe(false);
    expect(isNextPage("/api/session")).toBe(false);
    expect(isNextPage("mailto:privacy@beyrictech.com")).toBe(false);
  });
});
