import type { ReactNode } from "react";
import { Icon } from "@/components/ui";
import type { IconName } from "@/components/ui/icon";

type Tone = "teal" | "success" | "warning";

// The top of an account screen: an optional icon tile (52px, the designer's), the title and the
// line under it. `tile` is left out on the sign-in and sign-up forms, as in the prototype.
export function AuthHead({
  tile,
  tone = "teal",
  title,
  children,
}: {
  tile?: IconName;
  tone?: Tone;
  title: string;
  children?: ReactNode;
}) {
  return (
    <>
      {tile ? (
        <div className={`auth-tile${tone === "teal" ? "" : ` auth-tile-${tone}`}`}>
          <Icon name={tile} size={tone === "success" ? 26 : 24} stroke={tone === "success" ? 2.4 : 1.8} />
        </div>
      ) : null}
      <div>
        <h1 className="auth-title">{title}</h1>
        {children ? <p className="auth-sub">{children}</p> : null}
      </div>
    </>
  );
}
