import { AppLink } from "./link";

// `badge` is a count drawn at the right (the Review link's open items); nothing is drawn for 0.
// A screen reader hears "Review, 3 open", not a bare number.
export function NavLink({
  href,
  label,
  current = false,
  badge = 0,
  badgeLabel = "open",
}: {
  href: string;
  label: string;
  current?: boolean;
  badge?: number;
  badgeLabel?: string;
}) {
  return (
    <AppLink className="nav-link" href={href} aria-current={current ? "page" : undefined}>
      <span>{label}</span>
      {badge > 0 ? (
        <>
          <span className="nav-badge" aria-hidden="true">
            {badge > 99 ? "99+" : badge}
          </span>
          <span className="visually-hidden">, {badge} {badgeLabel}</span>
        </>
      ) : null}
    </AppLink>
  );
}
