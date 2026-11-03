import { describe, expect, it } from "vitest";
import { NEXT_PAGES, isNextPage } from "@/lib/routes";
import { config } from "@/proxy";
import { filesUnder, rel } from "./helpers";

describe("routes", () => {
  const pagePaths = filesUnder("app", ["page.tsx"])
    .map((p) => "/" + rel(p).replace(/^app\//, "").replace(/\/?page\.tsx$/, ""))
    .map((p) => (p === "/" ? "/" : p))
    .sort();

  it("lists exactly the pages under app/ in NEXT_PAGES", () => {
    expect([...NEXT_PAGES].sort()).toEqual(pagePaths);
  });

  it("has a proxy matcher equal to NEXT_PAGES, so only Next-rendered pages get the nonce CSP", () => {
    expect([...config.matcher].sort()).toEqual([...NEXT_PAGES].sort());
  });

  it("recognises Next pages with a query or hash, and nothing else", () => {
    expect(isNextPage("/privacy#google")).toBe(true);
    expect(isNextPage("/terms?x=1")).toBe(true);
    expect(isNextPage("/login")).toBe(false);
    expect(isNextPage("/privacy/extra")).toBe(false);
    expect(isNextPage("mailto:privacy@beyrictech.com")).toBe(false);
  });
});
