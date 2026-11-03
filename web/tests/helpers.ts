import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

export const WEB = join(__dirname, "..");
export const REPO = join(WEB, "..");

export function read(path: string): string {
  return readFileSync(path, "utf8");
}

// All files under `dir` (relative to web/) with one of the extensions, skipping build output.
export function filesUnder(dir: string, exts: string[]): string[] {
  const out: string[] = [];
  const walk = (current: string) => {
    for (const name of readdirSync(current)) {
      if (name === "node_modules" || name === ".next") continue;
      const full = join(current, name);
      if (statSync(full).isDirectory()) walk(full);
      else if (exts.some((e) => name.endsWith(e))) out.push(full);
    }
  };
  walk(join(WEB, dir));
  return out;
}

export function rel(path: string): string {
  return relative(WEB, path).split(sep).join("/");
}

// A response body captured from the running API (docs/fe-captures/owner-app/<file>).
export function captured<T>(file: string): T {
  return JSON.parse(read(join(REPO, "docs/fe-captures/owner-app", file))).response.body as T;
}
