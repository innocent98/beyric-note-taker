import type { ReactNode } from "react";

export function Card({
  title,
  flush = false,
  children,
}: {
  title?: string;
  flush?: boolean;
  children: ReactNode;
}) {
  return (
    <section className={`card${flush ? " card-flush" : ""}`}>
      {title ? <div className="card-title">{title}</div> : null}
      {children}
    </section>
  );
}
