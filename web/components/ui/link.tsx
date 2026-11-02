import Link from "next/link";
import type { AnchorHTMLAttributes } from "react";
import { isNextPage } from "@/lib/routes";

type Props = AnchorHTMLAttributes<HTMLAnchorElement> & { href: string };

// A page Next renders navigates client-side; everything else (FastAPI pages, mailto:, other sites)
// is a plain <a>, because a client navigation to a path Next does not own would only be proxied
// to FastAPI and then fall back to a full page load anyway.
export function AppLink({ href, children, ...rest }: Props) {
  if (isNextPage(href)) {
    return (
      <Link href={href} {...rest}>
        {children}
      </Link>
    );
  }
  return (
    <a href={href} {...rest}>
      {children}
    </a>
  );
}
