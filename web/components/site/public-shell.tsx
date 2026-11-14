import type { ReactNode } from "react";
import { AppLink, Button, Logo } from "@/components/ui";
import { getSession } from "@/lib/api/server";
import { LEGAL_NAME } from "@/lib/site";

// Header, footer and main landmark for the public pages the designs do not draw (the 404 page),
// built from the legal pages' header and footer so they match. The header offers "Open the app" to
// the logged-in owner (GET /api/session) and "Sign in" to everyone else, FastAPI being unreachable
// included. The logo leads to `/`, the landing page; "Open the app" leads to `/meetings`.
export async function PublicShell({ children }: { children: ReactNode }) {
  const owner = (await getSession())?.authenticated === true;
  return (
    <div className="lg">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="lg-header">
        <div className="lg-header-in">
          <Logo href="/" />
          <nav aria-label="Account">
            {owner ? (
              <Button href="/meetings" size="md">
                Open the app
              </Button>
            ) : (
              <Button href="/login" size="md">
                Sign in
              </Button>
            )}
          </nav>
        </div>
      </header>
      <main id="main">{children}</main>
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
