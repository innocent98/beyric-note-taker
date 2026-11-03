import type { ReactNode } from "react";

export function Card({
  title,
  flush = false,
  compact = false,
  children,
}: {
  title?: string;
  flush?: boolean;
  compact?: boolean;
  children: ReactNode;
}) {
  return (
    <section className={`card${flush ? " card-flush" : ""}${compact ? " card-compact" : ""}`}>
      {title ? <div className="card-title">{title}</div> : null}
      {children}
    </section>
  );
}
