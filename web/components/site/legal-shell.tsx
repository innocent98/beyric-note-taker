import type { ReactNode } from "react";
import { AppLink, Logo } from "@/components/ui";
import { LEGAL_NAME } from "@/lib/site";

// The frame of the privacy and terms pages from Privacy Policy.dc.html: a sticky header with the
// two legal pages as tabs, an "On this page" list that follows the reader, the text, and a footer.
// The text itself (components/legal/*) is the live legal text, not the prototype's sample words.
// The logo leads to `/`, the landing page, for everyone.
export function LegalShell({
  current,
  toc,
  children,
}: {
  current: "privacy" | "terms";
  toc: ReadonlyArray<readonly [string, string]>;
  children: ReactNode;
}) {
  return (
    <div className="lg">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="lg-header">
        <div className="lg-header-in">
          <Logo href="/" />
          <nav className="lg-tabs" aria-label="Legal">
            <AppLink href="/privacy" aria-current={current === "privacy" ? "page" : undefined}>
              Privacy
            </AppLink>
            <AppLink href="/terms" aria-current={current === "terms" ? "page" : undefined}>
              Terms
            </AppLink>
          </nav>
        </div>
      </header>
      <div className="lg-body">
        <aside className="lg-toc" aria-label="On this page">
          <div className="lg-toc-title">ON THIS PAGE</div>
          {toc.map(([id, title]) => (
            <a key={id} href={`#${id}`}>
              {title}
            </a>
          ))}
        </aside>
        <main id="main" className="lg-article">
          {children}
          <div className="lg-ask">
            Questions about this page? Email <a href="mailto:privacy@beyrictech.com">privacy@beyrictech.com</a>.
          </div>
        </main>
      </div>
      <footer className="lg-footer">
        <div className="lg-footer-in">
          <span>&copy; 2026 {LEGAL_NAME}</span>
          <span className="lg-footer-links">
            <AppLink href="/privacy">Privacy</AppLink>
            <AppLink href="/terms">Terms</AppLink>
            <AppLink href="mailto:privacy@beyrictech.com">Contact</AppLink>
          </span>
        </div>
      </footer>
    </div>
  );
}
