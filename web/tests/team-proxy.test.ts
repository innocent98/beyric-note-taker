import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import { proxy } from "@/proxy";

// proxy.ts hands the page's `?team=` to the server render as `x-active-team`, because the render's FIRST call
// (the session, with the sidebar badges) is made before the page has read its own address. The header is
// always rewritten from the address: a request that carries its own is not believed.

function seen(url: string, headers: Record<string, string> = {}): string | null {
  const response = proxy(new NextRequest(url, { headers }));
  // NextResponse.next({ request: { headers } }) lists the forwarded request headers.
  return response.headers.get("x-middleware-request-x-active-team");
}

describe("proxy: the team of the address", () => {
  it("forwards a valid ?team=", () => {
    expect(seen("https://notes.example.test/tasks?team=4")).toBe("4");
    expect(seen("https://notes.example.test/?from=2026-11-01&team=12")).toBe("12");
  });

  it("forwards nothing for no team, a damaged one, or one past the largest id", () => {
    for (const url of ["/", "/?team=abc", "/?team=0", "/?team=2147483648", "/?team="]) {
      expect(seen(`https://notes.example.test${url}`)).toBeNull();
    }
  });

  it("never believes a header the client sent itself", () => {
    expect(seen("https://notes.example.test/tasks", { "x-active-team": "9" })).toBeNull();
    expect(seen("https://notes.example.test/tasks?team=4", { "x-active-team": "9" })).toBe("4");
  });
});
