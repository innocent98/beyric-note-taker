import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from "react";
import { AppLink } from "./link";

export type ButtonKind = "primary" | "secondary" | "danger" | "ghost" | "link" | "quiet" | "outline-light";
export type ButtonSize = "sm" | "md" | "lg" | "xl" | "field" | "";

type Common = { kind?: ButtonKind; size?: ButtonSize; block?: boolean; wide?: boolean; children: ReactNode };
type AsButton = Common & { href?: undefined } & ButtonHTMLAttributes<HTMLButtonElement>;
type AsLink = Common & { href: string } & AnchorHTMLAttributes<HTMLAnchorElement> & {
    disabled?: boolean;
  };

export function buttonClass(kind: ButtonKind, size: ButtonSize, block: boolean, wide = false): string {
  return [
    "btn",
    kind === "primary" ? "" : `btn-${kind}`,
    size ? `btn-${size}` : "",
    block ? "btn-block" : "",
    wide ? "btn-wide" : "",
  ]
    .filter(Boolean)
    .join(" ");
}

// Renders a <button>, or an <a> when href is given (the same as the Jinja `button` macro).
export function Button(props: AsButton | AsLink) {
  const { kind = "primary", size = "", block = false, wide = false, children, ...rest } = props;
  const className = buttonClass(kind, size, block, wide);
  if (typeof rest.href === "string") {
    const { href, disabled, ...anchor } = rest as AsLink;
    return (
      <AppLink className={className} href={href} aria-disabled={disabled ? "true" : undefined} {...anchor}>
        {children}
      </AppLink>
    );
  }
  const { type = "submit", ...button } = rest as ButtonHTMLAttributes<HTMLButtonElement>;
  return (
    <button className={className} type={type} {...button}>
      {children}
    </button>
  );
}
