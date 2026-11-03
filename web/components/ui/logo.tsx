import { AppLink } from "./link";

type Props = { large?: boolean; href?: string | null; label?: boolean };

export function Logo({ large = false, href = "/", label = true }: Props) {
  const size = large ? 40 : 28;
  const content = (
    <>
      <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
        <rect width="32" height="32" rx="8" className="logo-tile" />
        <path
          d="M9 8H23a2 2 0 0 1 2 2V19a2 2 0 0 1-2 2H15L10.5 24.5V21H9a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2Z"
          className="logo-bubble"
        />
        <path d="M11 12.5h10M11 16.5h6" className="logo-lines" />
      </svg>
      {label ? (
        <span className="logo-word">
          <span>Beyric</span>
          <span className="logo-sub">Note Taker</span>
        </span>
      ) : null}
    </>
  );
  const className = `logo${large ? " logo-lg" : ""}`;
  if (href) {
    return (
      <AppLink className={className} href={href}>
        {content}
      </AppLink>
    );
  }
  return <span className={className}>{content}</span>;
}
