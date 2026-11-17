import { Logo } from "@/components/ui";
import { AppLink } from "@/components/ui/link";
import { homeFor, sectionHref } from "./public-links";
import { PublicNavLink } from "./public-nav-link";

// The one header of every public page (the landing page, pricing, privacy, terms, the 404 page): the
// designer's second Landing file (2026-11-14). Logo, the four section links, Sign in and Get started;
// a signed-in owner gets "Open the app" instead of the two sign-up links. Sticky. `signedIn` is the
// answer of GET /api/session, false when FastAPI cannot be reached. The owner's logo leads to /welcome
// (the landing page), since `/` is the owner's Meetings page; a visitor's to `/`.
export function PublicHeader({ signedIn = false }: { signedIn?: boolean }) {
  const home = homeFor(signedIn);
  return (
    <header className="ld-header">
      <div className="ld-header-in">
        <Logo size="lg" href={home} />
        <nav className="ld-nav" aria-label="Main">
          <PublicNavLink href={sectionHref(home, "product")}>Product</PublicNavLink>
          <PublicNavLink href={sectionHref(home, "teams")}>Who it&apos;s for</PublicNavLink>
          <PublicNavLink href="/pricing">Pricing</PublicNavLink>
          <PublicNavLink href={sectionHref(home, "security")}>Security</PublicNavLink>
        </nav>
        <div className="ld-header-actions">
          {signedIn ? (
            <AppLink className="ld-get-started" href="/">
              Open the app
            </AppLink>
          ) : (
            <>
              <AppLink className="ld-signin" href="/login">
                Sign in
              </AppLink>
              <AppLink className="ld-get-started" href="/signup">
                Get started
              </AppLink>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
