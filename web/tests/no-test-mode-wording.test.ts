// The owner's rule (2026-11-20): the app carries no wording that tells a person it is in test mode,
// so the experience is the same as in production. This reads the source of every screen, component
// and shared module and fails if such wording (or the API field that carried it) comes back.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");
const DIRS = ["app", "components", "lib"];
const FORBIDDEN =
  /test[ _-]?mode|testMode|test invoice|test keys?|sandbox|no real money|not a tax document|BN-T-/i;

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(tsx?|css|json|txt)$/.test(name) ? [path] : [];
  });
}

describe("no test-mode wording in the web app", () => {
  const files = DIRS.flatMap((dir) => sources(join(ROOT, dir)));

  it("reads a real set of files", () => {
    expect(files.length).toBeGreaterThan(50);
  });

  it("has none in any screen, component or shared module", () => {
    const hits = files
      .map((file) => ({ file, found: readFileSync(file, "utf8").match(FORBIDDEN) }))
      .filter((hit) => hit.found !== null)
      .map((hit) => `${hit.file.slice(ROOT.length + 1)}: ${hit.found?.[0]}`);
    expect(hits).toEqual([]);
  });
});
