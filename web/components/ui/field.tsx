import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { Icon, type IconName } from "./icon";

// `hideLabel` keeps the label for screen readers and drops it from the layout (a control whose
// purpose the surrounding text already shows, as in the designer's compact forms).
// `labelEnd` sits at the right of the label row (the "Forgot password?" link on a sign-in password).
type Shared = { id: string; label: string; hint?: string; error?: string; hideLabel?: boolean; labelEnd?: ReactNode };

// `large` is the sign-in size (44px, 15px text); `end` is a control drawn inside the right edge (the
// Show / Hide button on a password).

function Wrap({ id, label, hint, error, hideLabel, labelEnd, children }: Shared & { children: ReactNode }) {
  const note = error ?? hint;
  const labelEl = (
    <label className={hideLabel ? "field-label visually-hidden" : "field-label"} htmlFor={id}>
      {label}
    </label>
  );
  return (
    <div className={`field${error ? " field-error" : ""}`}>
      {labelEnd && !hideLabel ? (
        <div className="field-label-row">
          {labelEl}
          {labelEnd}
        </div>
      ) : (
        labelEl
      )}
      {children}
      {note ? <span className="field-hint">{note}</span> : null}
    </div>
  );
}

// A labelled text input. `error` replaces the hint, in the error colour. `icon` draws a line icon
// inside the left edge.
export function Field({
  id,
  label,
  hint,
  error,
  hideLabel,
  labelEnd,
  icon,
  large = false,
  end,
  name,
  type = "text",
  ...rest
}: Shared & { icon?: IconName; large?: boolean; end?: ReactNode } & Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "className">) {
  const input = <input className={large ? "input input-lg" : "input"} id={id} name={name ?? id} type={type} {...rest} />;
  return (
    <Wrap id={id} label={label} hint={hint} error={error} hideLabel={hideLabel} labelEnd={labelEnd}>
      {icon || end ? (
        <div className={`input-wrap${end ? " input-wrap-end" : ""}`}>
          {icon ? <Icon name={icon} /> : null}
          {input}
          {end}
        </div>
      ) : (
        input
      )}
    </Wrap>
  );
}

export function SelectField({
  id,
  label,
  hint,
  hideLabel,
  compact = false,
  options,
  name,
  ...rest
}: Omit<Shared, "error"> & {
  compact?: boolean;
  options: ReadonlyArray<readonly [string | number, string]>;
} & Omit<SelectHTMLAttributes<HTMLSelectElement>, "id" | "className" | "children">) {
  return (
    <Wrap id={id} label={label} hint={hint} hideLabel={hideLabel}>
      <select className={compact ? "select select-sm" : "select"} id={id} name={name ?? id} {...rest}>
        {options.map(([value, text]) => (
          <option key={value} value={value}>
            {text}
          </option>
        ))}
      </select>
    </Wrap>
  );
}

export function TextareaField({
  id,
  label,
  hint,
  error,
  hideLabel,
  name,
  rows = 5,
  ...rest
}: Shared & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id" | "className">) {
  return (
    <Wrap id={id} label={label} hint={hint} error={error} hideLabel={hideLabel}>
      <textarea className="textarea" id={id} name={name ?? id} rows={rows} {...rest} />
    </Wrap>
  );
}
