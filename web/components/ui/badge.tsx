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
  // A sentence (the bot's state can be one) may wrap; a short word stays on one line.
  const wraps = typeof children === "string" && children.length > 28;
  const className = ["badge", tone === "neutral" ? "" : `badge-${tone}`, dot ? "badge-dot" : "", small ? "badge-sm" : "", wraps ? "badge-wrap" : ""]
    .filter(Boolean)
    .join(" ");
  return <span className={className}>{children}</span>;
}
