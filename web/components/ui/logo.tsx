import { AppLink } from "./link";

type Size = "sm" | "md" | "lg";
type Props = {
  size?: Size;
  href?: string | null;
  label?: boolean;
  tone?: "light" | "dark";
};

// The mark from Brand.dc.html: a lowercase b drawn as one rounded stroke, the bowl stopping three
// quarters of the way round, with a dot at its centre. Tile sizes follow the designs: 26 in the
// footer, 28 in the app and legal header, 30 in the landing and sign-in header. The centre dot is
// dropped at 16px and below (the favicon draws that variant).
const TILE: Record<Size, number> = { sm: 26, md: 28, lg: 30 };

export function LogoMark({ size = 28, dot = true }: { size?: number; dot?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 120 120" aria-hidden="true" className="logo-mark">
      <rect width="120" height="120" rx="28" className="logo-tile" />
      <path d="M38 24V72A24 24 0 1 0 62 48" className="logo-glyph" strokeWidth={dot ? 14 : 16} />
      {dot ? <circle cx="62" cy="72" r="6" className="logo-dot" /> : null}
    </svg>
  );
}

export function Logo({ size = "md", href = "/", label = true, tone = "light" }: Props) {
  const content = (
    <>
      <LogoMark size={TILE[size]} />
      {label ? (
        <span className="logo-word">
          <span>Beyric</span>
          <span className="logo-sub">Note Taker</span>
        </span>
      ) : null}
    </>
  );
  const className = `logo logo-${size}${tone === "dark" ? " logo-dark" : ""}`;
  if (href) {
    return (
      <AppLink className={className} href={href}>
        {content}
      </AppLink>
    );
  }
  return <span className={className}>{content}</span>;
}
