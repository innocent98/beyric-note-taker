import type { ReactNode } from "react";
import { getSession } from "@/lib/api/server";
import { PublicFooter } from "./public-footer";
import { PublicHeader } from "./public-header";

// The frame of every public page: skip link, the one header, the `main` landmark and the one footer.
// app/(public)/layout.tsx puts it round every page of that route group (the landing page at /welcome,
// pricing, privacy, terms), so a public page added to the group gets it with no code of its own. The two
// public pages outside the group wrap themselves: `/` (it is the owner's Meetings page too, so it cannot
// sit in the group) and the 404 page (a layout does not wrap it).
// The header offers "Open the app" to the signed-in owner (GET /api/session) and "Sign in" to everyone
// else, FastAPI being unreachable included.
export async function PublicShell({ children }: { children: ReactNode }) {
  const signedIn = (await getSession())?.authenticated === true;
  return (
    <div className="ld">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <PublicHeader signedIn={signedIn} />
      <main id="main">{children}</main>
      <PublicFooter signedIn={signedIn} />
    </div>
  );
}
