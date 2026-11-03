import { NextResponse, type NextRequest } from "next/server";
import { buildCsp } from "@/lib/csp";

// Runs only for the pages Next owns (the matcher below), so responses proxied from FastAPI keep
// FastAPI's own Content-Security-Policy and headers untouched. Keep the matcher equal to
// NEXT_PAGES in lib/routes.ts (tests/routes.test.ts checks).
export function proxy(request: NextRequest) {
  const nonce = btoa(crypto.randomUUID());
  const csp = buildCsp(nonce, process.env.NODE_ENV !== "production");

  // Next reads the nonce from the request's CSP header and stamps it on its own scripts.
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  // Same as FastAPI's own headers, so a visitor sees one policy across both.
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}

export const config = {
  matcher: [
    "/",
    "/login",
    "/signup",
    "/verify-email",
    "/forgot-password",
    "/reset-password",
    "/pilot-full",
    "/meetings/:id",
    "/templates",
    "/vocabulary",
    "/privacy",
    "/terms",
    "/styleguide",
  ],
};
