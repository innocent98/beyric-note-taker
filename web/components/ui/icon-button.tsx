import type { ButtonHTMLAttributes } from "react";
import { Icon, type IconName } from "./icon";

type Props = { icon: IconName; label: string; tone?: "neutral" | "danger" } & Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  "className" | "children" | "aria-label" | "title"
>;

// A square button that holds only an icon. `label` is its accessible name and its tooltip.
export function IconButton({ icon, label, tone = "neutral", type = "button", ...rest }: Props) {
  return (
    <button className={`icon-btn${tone === "danger" ? " icon-btn-danger" : ""}`} type={type} aria-label={label} title={label} {...rest}>
      <Icon name={icon} />
    </button>
  );
}
