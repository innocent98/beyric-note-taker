import type { ReactNode } from "react";
import { Icon, type IconName } from "./icon";

export type AlertKind = "error" | "notice" | "info";

const ICON: Partial<Record<AlertKind, IconName>> = { error: "info", notice: "warning" };

// The designer's inline message: an error (red, circle icon), a notice (amber, triangle icon) or
// plain information (teal wash, no icon).
export function Alert({
  kind = "info",
  role = "note",
  children,
}: {
  kind?: AlertKind;
  role?: string;
  children: ReactNode;
}) {
  const icon = ICON[kind];
  return (
    <div className={`alert alert-${kind}`} role={kind === "error" ? "alert" : role}>
      {icon ? <Icon name={icon} /> : null}
      <div>{children}</div>
    </div>
  );
}
