import type { ReactNode } from "react";

export type AlertKind = "error" | "notice" | "info";

export function Alert({
  kind = "info",
  role = "note",
  children,
}: {
  kind?: AlertKind;
  role?: string;
  children: ReactNode;
}) {
  return (
    <div className={`alert alert-${kind}`} role={kind === "error" ? "alert" : role}>
      {children}
    </div>
  );
}
