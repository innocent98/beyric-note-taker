"use client";

import { Button, Logo } from "@/components/ui";

// Shown when a page cannot load its data (FastAPI unreachable, a gateway error). The designs draw
// no error page, so it uses the sign-in page's type and the empty-state card of the meeting page.
export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="state-page">
      <Logo size="lg" href={null} />
      <section className="card state-card">
        <span className="state-tile state-danger" aria-hidden="true" />
        <h1 className="state-title">Something went wrong</h1>
        <p>The page could not be loaded. Nothing you did has been lost.</p>
        <Button type="button" onClick={reset}>
          Try again
        </Button>
      </section>
    </div>
  );
}
