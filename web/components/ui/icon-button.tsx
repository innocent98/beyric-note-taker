import type { ButtonHTMLAttributes } from "react";
import { Icon, type IconName } from "./icon";

type Props = { icon: IconName; label: string; tooltip?: string; tone?: "neutral" | "danger" } & Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "className" | "children" | "aria-label" | "title"
>;

// A square button that holds only an icon. `label` is its accessible name and its tooltip, unless
// `tooltip` gives the tooltip shorter words (the designer's "Remove" on a row whose name says which).
export function IconButton({ icon, label, tooltip, tone = "neutral", type = "button", ...rest }: Props) {
  return (
    <button className={`icon-btn${tone === "danger" ? " icon-btn-danger" : ""}`} type={type} aria-label={label} title={tooltip ?? label} {...rest}>
      <Icon name={icon} />
    </button>
  );
}
