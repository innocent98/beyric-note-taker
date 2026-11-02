import { AppLink } from "./link";

export type TabItem = { href: string; label: string; current?: boolean };

// Each tab is a real link, so each tab is a page.
export function Tabs({ items }: { items: ReadonlyArray<TabItem> }) {
  return (
    <nav className="tabs" aria-label="Sections">
      {items.map((item) => (
        <AppLink
          key={item.label}
          className="tab"
          href={item.href}
          aria-current={item.current ? "page" : undefined}
        >
          {item.label}
        </AppLink>
      ))}
    </nav>
  );
}
