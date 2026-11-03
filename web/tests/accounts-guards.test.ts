import { describe, expect, it } from "vitest";
import { APP_PATHS, NEXT_PAGES } from "@/lib/routes";
import { join } from "node:path";
import { filesUnder, read, rel, WEB } from "./helpers";

describe("account screen guards", () => {
  const forms = [...filesUnder("components/auth", [".tsx"]), ...filesUnder("components/app", ["login-form.tsx"])];

  it("posts every account form, so a submit before the page is interactive never puts a password in the URL", () => {
    const tags = forms.flatMap((p) => [...read(p).matchAll(/<form\b[^>]*>/g)].map((m) => ({ file: rel(p), tag: m[0] })));
    expect(tags.length).toBeGreaterThan(5);
    expect(tags.filter(({ tag }) => !/method="post"/.test(tag))).toEqual([]);
  });

  it("keeps the mailed-link pages out of the crawlers' reach", () => {
    for (const page of ["/signup", "/verify-email", "/forgot-password", "/reset-password", "/pilot-full"]) {
      expect(NEXT_PAGES).toContain(page);
      expect(APP_PATHS).toContain(page);
    }
  });

  it("sends every public account write as a write with the CSRF token", () => {
    const client = read(join(WEB, "lib/api/client.ts"));
    for (const path of ["/api/signup", "/api/signup/resend", "/api/verify-email", "/api/password/forgot", "/api/password/reset", "/api/waitlist"]) {
      const line = client.split("\n").join(" ").match(new RegExp(`"POST", "${path}"[^)]*\\)`))?.[0] ?? "";
      expect(line, path).toContain("write: true");
    }
  });
});
