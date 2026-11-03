import type { Metadata } from "next";
import { Button } from "@/components/ui";
import { PublicShell } from "@/components/site/public-shell";

export const metadata: Metadata = { title: "Page not found", robots: { index: false } };

export default function NotFound() {
  return (
    <PublicShell>
      <div className="notfound">
        <h1>Page not found</h1>
        <p className="muted">That page does not exist.</p>
        <Button href="/">Go to the home page</Button>
      </div>
    </PublicShell>
  );
}
