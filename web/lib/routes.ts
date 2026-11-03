// Pages Next renders itself. Every other path is proxied to FastAPI (next.config.ts).
// proxy.ts must list the same HTML paths in its matcher (a literal there, so tests/routes.test.ts
// compares); the sitemap and robots rules also read from here.
export const NEXT_PAGES = ["/privacy", "/terms", "/welcome", "/styleguide"] as const;

export function isNextPage(href: string): boolean {
  const path = href.split(/[?#]/, 1)[0];
  return (NEXT_PAGES as readonly string[]).includes(path);
}

// Paths of the owner app that crawlers must not visit (FastAPI-served for now).
export const APP_PATHS = [
  "/login",
  "/logout",
  "/meetings",
  "/templates",
  "/vocabulary",
  "/styleguide",
  "/api/",
] as const;
