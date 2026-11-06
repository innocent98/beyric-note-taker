import { describe, expect, it } from "vitest";
import { BRAND, PROJECT_COLOURS } from "@/lib/brand";
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

  it("keeps the six project colours in lib/brand.ts equal to --swatch-1 to --swatch-6", () => {
    const tokens = read(join(WEB, "styles/tokens.css"));
    const swatches = [1, 2, 3, 4, 5, 6].map((n) => new RegExp(`--swatch-${n}:\\s*(#[0-9A-Fa-f]{6})`).exec(tokens)?.[1]?.toUpperCase());
    expect(swatches).toEqual(PROJECT_COLOURS.map((c) => c.toUpperCase()));
  });

  it("keeps lib/brand.ts equal to tokens.css", () => {
    const tokens = read(join(WEB, "styles/tokens.css"));
    const value = (name: string) => new RegExp(`--${name}:\\s*(#[0-9A-Fa-f]{6})`).exec(tokens)?.[1]?.toUpperCase();
    expect(BRAND.teal.toUpperCase()).toBe(value("teal"));
    expect(BRAND.bgLight.toUpperCase()).toBe(value("bg"));
  });

  it("uses no inline style prop or attribute, and no inline HTML event handler attributes", () => {
    // The CSP forbids inline styles, and inline styling is how screens drift from the components.
    const files = filesUnder("app", [".tsx"]).concat(filesUnder("components", [".tsx"]));
    const offenders = files
      .filter((p) => /\bstyle\s*=|\bon[a-z]+\s*=/.test(read(p)) && !rel(p).endsWith("behaviors.tsx"))
      .map(rel);
    expect(offenders).toEqual([]);
  });

  it("puts React event props (onClick, onSubmit) only in client components", () => {
    // A server component cannot take one (Next fails the build); this names the rule for a reader.
    const offenders = filesUnder("app", [".tsx"])
      .concat(filesUnder("components", [".tsx"]))
      .filter((p) => /\bon[A-Z][A-Za-z]*\s*=/.test(read(p)) && !/^\s*"use client"/.test(read(p)))
      .map(rel);
    expect(offenders).toEqual([]);
  });

  it("uses dangerouslySetInnerHTML only for the sanitised notes and the JSON-LD block", () => {
    const allowed = ["components/app/notes-html.tsx", "components/site/json-ld.tsx"];
    const offenders = filesUnder("app", [".tsx"])
      .concat(filesUnder("components", [".tsx"]))
      .filter((p) => read(p).includes("dangerouslySetInnerHTML") && !allowed.includes(rel(p)))
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
