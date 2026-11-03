import type { ReactNode } from "react";
import { AppLink, Button, Logo } from "@/components/ui";
import { getSession } from "@/lib/api/server";
import { LEGAL_NAME } from "@/lib/site";

// Header, footer and main landmark for the public pages. The header offers "Open the app" to the
// logged-in owner (GET /api/session) and "Sign in" to everyone else, FastAPI being unreachable
// included.
export async function PublicShell({ children, mainClass = "pub-main" }: { children: ReactNode; mainClass?: string }) {
  const owner = (await getSession())?.authenticated === true;
  return (
    <div className="bg-surface">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="pub-bar">
        <Logo />
        <nav className="pub-bar-actions" aria-label="Account">
          {owner ? <Button href="/">Open the app</Button> : <Button href="/login">Sign in</Button>}
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
