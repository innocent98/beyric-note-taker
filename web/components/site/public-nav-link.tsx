"use client";

import type { AnchorHTMLAttributes } from "react";
import { usePathname } from "next/navigation";
import { AppLink } from "@/components/ui/link";

// A link of the public header or footer that marks itself `aria-current="page"` on its own page, so a
// public page added later is marked with no code of its own. A link with a `#section` is never current.
export function PublicNavLink({ href, children, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const pathname = usePathname();
  const current = !href.includes("#") && pathname === href;
  return (
    <AppLink href={href} aria-current={current ? "page" : undefined} {...rest}>
      {children}
    </AppLink>
  );
}
