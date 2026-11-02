import type { ReactNode } from "react";
import { AppLink, Button, Logo } from "@/components/ui";
import { LEGAL_NAME } from "@/lib/site";

// Header, footer and main landmark for the public pages. "Sign in" is always shown: a logged-in
// owner is only recognised by FastAPI today (the header switches to "Open the app" once
// GET /api/session exists).
export function PublicShell({ children, mainClass = "pub-main" }: { children: ReactNode; mainClass?: string }) {
  return (
    <div className="bg-surface">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="pub-bar">
        <Logo />
        <nav className="pub-bar-actions" aria-label="Account">
          <Button href="/login">Sign in</Button>
        </nav>
      </header>
      <main id="main" className={mainClass}>
        {children}
      </main>
      <footer className="pub-foot">
        <span>&copy; 2026 {LEGAL_NAME}</span>
        <span className="pub-foot-links">
          <AppLink href="/privacy">Privacy</AppLink>
          <AppLink href="/terms">Terms</AppLink>
          <AppLink href="mailto:privacy@beyrictech.com">Contact</AppLink>
        </span>
      </footer>
    </div>
  );
}
