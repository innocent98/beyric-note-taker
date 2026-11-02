// Content-Security-Policy for pages Next renders. Same strictness as FastAPI's (app/main.py): no
// 'unsafe-inline' for scripts or styles, fonts and images from this origin only. Differences,
// each needed by Next: a per-request nonce (+ 'strict-dynamic') so Next's own bootstrap scripts
// run, and connect-src 'self' because client navigation fetches RSC payloads.
export function buildCsp(nonce: string, dev = false): string {
  const script = ["'self'", `'nonce-${nonce}'`, "'strict-dynamic'"];
  const style = ["'self'"];
  if (dev) {
    // `next dev` evaluates code for hot reload and injects inline styles. Never in production.
    script.push("'unsafe-eval'");
    style.push("'unsafe-inline'");
  }
  return [
    "default-src 'none'",
    `script-src ${script.join(" ")}`,
    `style-src ${style.join(" ")}`,
    "img-src 'self'",
    "font-src 'self'",
    "connect-src 'self'",
    "manifest-src 'self'",
    "form-action 'self'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
  ].join("; ");
}
