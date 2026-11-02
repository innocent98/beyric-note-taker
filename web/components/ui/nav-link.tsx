import { AppLink } from "./link";

export function NavLink({ href, label, current = false }: { href: string; label: string; current?: boolean }) {
  return (
    <AppLink className="nav-link" href={href} aria-current={current ? "page" : undefined}>
      {label}
    </AppLink>
  );
}
