#!/usr/bin/env node
// Loads pages in a real Chromium and fails on any console error or Content-Security-Policy
// violation, and proves the page hydrated and the CSP header is the strict one.
//
//   PLAYWRIGHT=/path/to/playwright/index.mjs node scripts/browser-check.mjs http://127.0.0.1:3000 / /privacy
//
// Playwright is not a dependency of this app (CI does not run this); point PLAYWRIGHT at an
// installed copy, or leave it unset to use `import("playwright")`.
const [base, ...paths] = process.argv.slice(2);
if (!base || paths.length === 0) {
  console.error("usage: browser-check.mjs <base-url> <path>...");
  process.exit(2);
}
const { chromium } = await import(process.env.PLAYWRIGHT ?? "playwright");
const browser = await chromium.launch();
let failed = 0;

for (const scheme of ["light", "dark"]) {
  const context = await browser.newContext({ colorScheme: scheme });
  for (const path of paths) {
    const page = await context.newPage();
    const problems = [];
    page.on("console", (m) => {
      if (m.type() === "error" || m.type() === "warning") problems.push(`console.${m.type()}: ${m.text()}`);
    });
    page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
    page.on("requestfailed", (r) => {
      // Next cancels a link prefetch when the visitor has already moved on; that is not an error.
      if (r.url().includes("_rsc=") && r.failure()?.errorText === "net::ERR_ABORTED") return;
      problems.push(`request failed: ${r.url()} ${r.failure()?.errorText}`);
    });
    await page.addInitScript(() => {
      window.__csp = [];
      document.addEventListener("securitypolicyviolation", (e) =>
        window.__csp.push(`${e.violatedDirective} blocked ${e.blockedURI || "inline"}`));
    });
    const response = await page.goto(base + path, { waitUntil: "networkidle" });
    const csp = response.headers()["content-security-policy"] ?? "";
    const info = await page.evaluate(() => ({
      violations: window.__csp,
      hydrated: typeof window.next === "object" || Array.isArray(window.__next_f),
      nonceScripts: [...document.scripts].filter((s) => s.nonce).length,
      // <next-route-announcer> gets its style from script (CSSOM), which CSP allows; it is not markup.
      inlineStyleAttrs: document.querySelectorAll("[style]:not(next-route-announcer)").length,
      inlineStyleTags: document.querySelectorAll("style").length,
      font: document.fonts.check('16px "Geist"'),
      title: document.title,
    }));
    for (const v of info.violations) problems.push(`CSP violation: ${v}`);
    if (!/script-src 'self' 'nonce-[^']+' 'strict-dynamic'/.test(csp)) problems.push(`unexpected CSP header: ${csp}`);
    if (/unsafe-(inline|eval)/.test(csp)) problems.push("CSP allows unsafe-inline or unsafe-eval");
    if (!info.hydrated) problems.push("page did not hydrate (no Next client bootstrap)");
    if (info.nonceScripts === 0) problems.push("no script carries the nonce");
    if (info.inlineStyleAttrs || info.inlineStyleTags) problems.push(`inline styles: ${info.inlineStyleAttrs} attributes, ${info.inlineStyleTags} tags`);
    console.log(`${problems.length ? "FAIL" : "PASS"}  ${scheme.padEnd(5)} ${path}  status ${response.status()}  title "${info.title}"  nonce scripts ${info.nonceScripts}  Geist loaded ${info.font}`);
    for (const p of problems) console.log(`        ${p}`);
    if (problems.length) failed++;
    await page.close();
  }
  await context.close();
}

// Client navigation: following a link between two Next pages must not reload the document.
const page = await browser.newPage();
await page.goto(base + paths[0], { waitUntil: "networkidle" });
await page.evaluate(() => { window.__marker = "same-document"; });
const link = page.locator('footer a[href="/terms"], footer a[href="/privacy"]').first();
if (await link.count()) {
  await link.click();
  await page.waitForLoadState("networkidle");
  const kept = await page.evaluate(() => window.__marker === "same-document");
  console.log(`${kept ? "PASS" : "FAIL"}  client-side navigation keeps the document (${page.url()})`);
  if (!kept) failed++;
}
await browser.close();
process.exit(failed ? 1 : 0);
