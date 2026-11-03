import type { ReactNode } from "react";

export type BadgeTone = "neutral" | "success" | "danger" | "warning" | "info" | "brand";

// `dot` adds the small status dot the designer's meeting list and meeting page use.
export function Badge({
  tone = "neutral",
  dot = false,
  children,
}: {
  tone?: BadgeTone;
  dot?: boolean;
  children: ReactNode;
}) {
  const className = ["badge", tone === "neutral" ? "" : `badge-${tone}`, dot ? "badge-dot" : ""]
    .filter(Boolean)
    .join(" ");
  return <span className={className}>{children}</span>;
}
