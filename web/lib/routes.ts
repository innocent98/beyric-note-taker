// Pages Next renders itself. Every other path is proxied to FastAPI (next.config.ts), so removing
// a path from this list, from proxy.ts's matcher and from app/ hands it back to the Jinja page.
// proxy.ts must list the same HTML paths in its matcher (a literal there, so tests/routes.test.ts
// compares); the sitemap and robots rules also read from here. `[id]` is one path segment (the
// matcher spells it `:id`).
export const NEXT_PAGES = [
  "/",
  "/login",
  "/signup",
  "/verify-email",
  "/unsubscribe",
  "/forgot-password",
  "/reset-password",
  "/pilot-full",
  "/meetings/[id]",
  "/tasks",
  "/projects",
  "/team",
  "/accept-invite",
  "/decisions",
  "/review",
  "/share",
  "/templates",
  "/vocabulary",
  "/settings",
  "/notifications",
  "/privacy",
  "/terms",
  "/styleguide",
] as const;

// Handlers Next owns that are not pages (no nonce CSP needed, no HTML). `/logout` takes a POST,
// calls DELETE /api/session with the visitor's cookie and passes FastAPI's Set-Cookie on.
// `/share-target` takes the PWA share sheet's POST and redirects to /share (app/share-target/route.ts).
export const NEXT_ROUTES = ["/logout", "/share-target"] as const;

const PATTERNS = NEXT_PAGES.map(
  (page) => new RegExp(`^${page.replace(/\[[^\]]+\]/g, "[^/]+").replace(/\//g, "\\/")}$`),
);

export function isNextPage(href: string): boolean {
  const path = href.split(/[?#]/, 1)[0];
  return PATTERNS.some((pattern) => pattern.test(path));
}

// Paths of the owner app that crawlers must not visit.
export const APP_PATHS = [
  "/login",
  "/signup",
  "/verify-email",
  "/unsubscribe",
  "/forgot-password",
  "/reset-password",
  "/pilot-full",
  "/logout",
  "/meetings",
  "/tasks",
  "/team",
  "/accept-invite",
  "/decisions",
  "/review",
  "/share",
  "/templates",
  "/vocabulary",
  "/settings",
  "/notifications",
  "/styleguide",
  "/api/",
] as const;
