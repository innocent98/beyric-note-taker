import type { ReactNode } from "react";

export type BadgeTone = "neutral" | "success" | "danger" | "warning" | "info" | "brand";

// `dot` adds the small status dot the designer's meeting list and meeting page use; `small` is the
// 11px pill drawn inside the product previews on the landing and sign-in pages.
export function Badge({
  tone = "neutral",
  dot = false,
  small = false,
  children,
}: {
  tone?: BadgeTone;
  dot?: boolean;
  small?: boolean;
  children: ReactNode;
}) {
  const className = ["badge", tone === "neutral" ? "" : `badge-${tone}`, dot ? "badge-dot" : "", small ? "badge-sm" : ""]
    .filter(Boolean)
    .join(" ");
  return <span className={className}>{children}</span>;
}
