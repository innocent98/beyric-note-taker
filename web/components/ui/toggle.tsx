import type { ButtonHTMLAttributes } from "react";

// The designer's switch (Beyric Note Taker.dc.html, Settings): a 36 x 20 pill, teal when on, with a
// 16 px white knob. It is a button with role="switch" so a keyboard and a screen reader can use it;
// the caller names it with `aria-labelledby` (the row's label) and may add `aria-describedby`.
export function Toggle({
  checked,
  ...rest
}: { checked: boolean } & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "role" | "aria-checked" | "className" | "children" | "type">) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      className={`switch${checked ? " switch-on" : ""}`}
      {...rest}
    >
      <span className="switch-knob" aria-hidden="true" />
    </button>
  );
}
