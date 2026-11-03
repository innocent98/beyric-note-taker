import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import { buildCsp } from "@/lib/csp";
import { proxy } from "@/proxy";

const directive = (csp: string, name: string) =>
  csp.split("; ").find((d) => d.startsWith(`${name} `)) ?? "";

describe("buildCsp", () => {
  const csp = buildCsp("abc123");

  it("allows scripts only with the nonce, and no unsafe-inline or unsafe-eval anywhere", () => {
    expect(directive(csp, "script-src")).toBe("script-src 'self' 'nonce-abc123' 'strict-dynamic'");
    expect(csp).not.toContain("unsafe-inline");
    expect(csp).not.toContain("unsafe-eval");
  });

  it("is as strict as FastAPI's policy (app/main.py), plus what Next needs", () => {
    expect(directive(csp, "style-src")).toBe("style-src 'self'");
    expect(csp).toContain("default-src 'none'");
    expect(csp).toContain("font-src 'self'");
    expect(csp).toContain("img-src 'self'");
    expect(csp).toContain("form-action 'self'");
    expect(csp).toContain("base-uri 'none'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("connect-src 'self'");
  });

  it("relaxes only in development", () => {
    const dev = buildCsp("abc123", true);
    expect(dev).toContain("'unsafe-eval'");
    expect(dev).toContain("style-src 'self' 'unsafe-inline'");
  });
});

describe("proxy", () => {
  it("puts a fresh nonce CSP on the response, and the same headers FastAPI sets", () => {
    const first = proxy(new NextRequest("http://localhost:3000/privacy"));
    const second = proxy(new NextRequest("http://localhost:3000/privacy"));
    const a = first.headers.get("content-security-policy") ?? "";
    const b = second.headers.get("content-security-policy") ?? "";
    expect(a).toMatch(/script-src 'self' 'nonce-[A-Za-z0-9+/=]+' 'strict-dynamic'/);
    expect(a).not.toBe(b);
    expect(first.headers.get("x-content-type-options")).toBe("nosniff");
    expect(first.headers.get("referrer-policy")).toBe("no-referrer");
  });
});
