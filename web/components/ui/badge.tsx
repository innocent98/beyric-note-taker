import type { ReactNode } from "react";

export type BadgeTone = "neutral" | "success" | "danger" | "warning" | "info" | "brand";

// `dot` adds the small status dot the designer's meeting list and meeting page use; `small` is the
// 11px pill drawn inside the product previews on the landing and sign-in pages. `live` makes the
// badge a polite live region, so a screen reader hears its text change without focus moving (the
// meeting page's state, which the page keeps refreshing).
export function Badge({
  tone = "neutral",
  dot = false,
  small = false,
  live = false,
  children,
}: {
  tone?: BadgeTone;
  dot?: boolean;
  small?: boolean;
  live?: boolean;
  children: ReactNode;
}) {
  // A sentence (the bot's state can be one) may wrap; a short word stays on one line.
  const wraps = typeof children === "string" && children.length > 28;
  const className = ["badge", tone === "neutral" ? "" : `badge-${tone}`, dot ? "badge-dot" : "", small ? "badge-sm" : "", wraps ? "badge-wrap" : ""]
    .filter(Boolean)
    .join(" ");
  return live ? (
    <span className={className} role="status" aria-live="polite">
      {children}
    </span>
  ) : (
    <span className={className}>{children}</span>
  );
}
