"use client";

import { Button, Card, Logo } from "@/components/ui";

// Shown when a page cannot load its data (FastAPI unreachable, a gateway error).
export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="auth">
      <Logo large href={null} />
      <div className="auth-card">
        <Card title="Something went wrong">
          <p className="muted">The page could not be loaded. Nothing you did has been lost.</p>
          <Button type="button" onClick={reset}>
            Try again
          </Button>
        </Card>
      </div>
    </div>
  );
}
