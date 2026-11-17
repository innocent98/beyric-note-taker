import type { ReactNode } from "react";
import { CONTACT_HREF } from "@/components/site/public-links";

// The body of the privacy and terms pages from Privacy Policy.dc.html: an "On this page" list that
// follows the reader beside the text. The header, the footer and the `main` landmark are the public
// shell's (app/(public)/layout.tsx), the same as on every public page. The text itself
// (components/legal/*) is the live legal text, not the prototype's sample words.
export function LegalPage({ toc, children }: { toc: ReadonlyArray<readonly [string, string]>; children: ReactNode }) {
  return (
    <div className="lg-body">
      <aside className="lg-toc" aria-label="On this page">
        <div className="lg-toc-title">ON THIS PAGE</div>
        {toc.map(([id, title]) => (
          <a key={id} href={`#${id}`}>
            {title}
          </a>
        ))}
      </aside>
      <article className="lg-article">
        {children}
        <div className="lg-ask">
          Questions about this page? Email <a href={CONTACT_HREF}>privacy@beyrictech.com</a>.
        </div>
      </article>
    </div>
  );
}
