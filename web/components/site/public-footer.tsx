import { Logo } from "@/components/ui";
import { LEGAL_NAME } from "@/lib/site";
import { CONTACT_HREF, homeFor, sectionHref } from "./public-links";
import { PublicNavLink } from "./public-nav-link";

// The one footer of every public page: the designer's landing footer (brand line, Product and Company
// columns, the entity and the address). The section links lead to the landing page from any page.
export function PublicFooter({ signedIn = false }: { signedIn?: boolean }) {
  const home = homeFor(signedIn);
  return (
    <footer className="ld-footer">
      <div className="ld-footer-in">
        <div className="ld-footer-cols">
          <div className="ld-footer-brand">
            <Logo size="sm" href={home} />
            <p>Meeting notes, decisions and tasks for teams, from {LEGAL_NAME}.</p>
          </div>
          <div className="ld-footer-col">
            <span>Product</span>
            <PublicNavLink href={sectionHref(home, "product")}>Product</PublicNavLink>
            <PublicNavLink href={sectionHref(home, "features")}>Features</PublicNavLink>
            <PublicNavLink href="/pricing">Pricing</PublicNavLink>
            <PublicNavLink href={sectionHref(home, "security")}>Security</PublicNavLink>
          </div>
          <div className="ld-footer-col">
            <span>Company</span>
            <PublicNavLink href="/privacy">Privacy</PublicNavLink>
            <PublicNavLink href="/terms">Terms</PublicNavLink>
            <PublicNavLink href={CONTACT_HREF}>Contact</PublicNavLink>
          </div>
        </div>
        <div className="ld-footer-base">
          <span>&copy; 2026 {LEGAL_NAME}</span>
          <span>notes.beyrictech.com</span>
        </div>
      </div>
    </footer>
  );
}
