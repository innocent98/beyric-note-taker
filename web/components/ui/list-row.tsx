import type { ReactNode } from "react";
import { AppLink } from "./link";

// One row of a list: a link area on the left (children), an action area on the right (`end`).
export function ListRow({ href, end, children }: { href?: string; end?: ReactNode; children: ReactNode }) {
  return (
    <li className="list-row">
      {href ? (
        <AppLink className="list-row-link" href={href}>
          {children}
        </AppLink>
      ) : (
        <div className="list-row-link">{children}</div>
      )}
      {end}
    </li>
  );
}
