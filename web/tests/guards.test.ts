import { describe, expect, it } from "vitest";
import { BRAND } from "@/lib/brand";
import { filesUnder, read, rel, WEB } from "./helpers";
import { join } from "node:path";

// Ported from tests/test_ui_guards.py: cheap checks that keep every screen on one design system.

const HEX_COLOUR = /#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{8}\b/;
const COLOUR_FUNCTION = /\b(?:rgb|rgba|hsl|hsla)\(/;

const sources = [
  ...filesUnder("app", [".css", ".ts", ".tsx"]),
  ...filesUnder("components", [".css", ".ts", ".tsx"]),
  ...filesUnder("lib", [".ts", ".tsx"]),
  ...filesUnder("styles", [".css"]),
  join(WEB, "proxy.ts"),
  join(WEB, "next.config.ts"),
];

describe("design guards", () => {
  it("has the design files", () => {
    const css = filesUnder("styles", [".css"]).map(rel);
    expect(css).toEqual(expect.arrayContaining(["styles/tokens.css", "styles/components.css", "styles/pages.css"]));
  });

  it("holds no colour literal outside tokens.css (and lib/brand.ts, for the manifest)", () => {
    const offenders = sources
      .filter((p) => rel(p) !== "styles/tokens.css" && rel(p) !== "lib/brand.ts")
      .filter((p) => {
        const text = read(p);
        return HEX_COLOUR.test(text) || COLOUR_FUNCTION.test(text);
      })
      .map(rel);
    expect(offenders, "add a token to tokens.css instead").toEqual([]);
  });

  it("keeps lib/brand.ts equal to tokens.css", () => {
    const tokens = read(join(WEB, "styles/tokens.css"));
    const value = (name: string, from = 0) =>
      new RegExp(`--${name}:\\s*(#[0-9A-Fa-f]{6})`).exec(tokens.slice(from))?.[1]?.toUpperCase();
    const darkStart = tokens.indexOf("prefers-color-scheme: dark");
    expect(BRAND.teal.toUpperCase()).toBe(value("teal"));
    expect(BRAND.bgLight.toUpperCase()).toBe(value("bg"));
    expect(BRAND.bgDark.toUpperCase()).toBe(value("bg", darkStart));
  });

  it("uses no inline style prop or attribute, and no inline event handlers", () => {
    // The CSP forbids inline styles, and inline styling is how screens drift from the components.
    const offenders = filesUnder("app", [".tsx"])
      .concat(filesUnder("components", [".tsx"]))
      .filter((p) => /\bstyle\s*=|\bon[A-Z][A-Za-z]*\s*=/.test(read(p)) && !rel(p).endsWith("behaviors.tsx"))
      .map(rel);
    expect(offenders).toEqual([]);
  });

  it("shows every component in the style guide", () => {
    const index = read(join(WEB, "components/ui/index.ts"));
    const names = [...index.matchAll(/export \{([^}]+)\}/g)]
      .flatMap((m) => m[1].split(","))
      .map((n) => n.trim())
      .filter(Boolean);
    const guide = read(join(WEB, "app/styleguide/page.tsx"));
    expect(names.length).toBeGreaterThan(10);
    const missing = names.filter((n) => !new RegExp(`<${n}[\\s/>]`).test(guide));
    // AppLink is the building block under every link component, shown by all of them.
    expect(missing.filter((n) => n !== "AppLink")).toEqual([]);
  });

  it("exports every file in components/ui from its index", () => {
    const index = read(join(WEB, "components/ui/index.ts"));
    const files = filesUnder("components/ui", [".tsx"]).map((p) => rel(p).replace(/^components\/ui\/|\.tsx$/g, ""));
    const missing = files.filter((f) => !index.includes(`"./${f}"`));
    expect(missing).toEqual([]);
  });
});
