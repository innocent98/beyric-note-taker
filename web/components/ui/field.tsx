import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { Icon, type IconName } from "./icon";

// `hideLabel` keeps the label for screen readers and drops it from the layout (a control whose
// purpose the surrounding text already shows, as in the designer's compact forms).
type Shared = { id: string; label: string; hint?: string; error?: string; hideLabel?: boolean };

function Wrap({ id, label, hint, error, hideLabel, children }: Shared & { children: ReactNode }) {
  const note = error ?? hint;
  return (
    <div className={`field${error ? " field-error" : ""}`}>
      <label className={hideLabel ? "field-label visually-hidden" : "field-label"} htmlFor={id}>
        {label}
      </label>
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
  icon,
  name,
  type = "text",
  ...rest
}: Shared & { icon?: IconName } & Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "className">) {
  const input = <input className="input" id={id} name={name ?? id} type={type} {...rest} />;
  return (
    <Wrap id={id} label={label} hint={hint} error={error} hideLabel={hideLabel}>
      {icon ? (
        <div className="input-wrap">
          <Icon name={icon} />
          {input}
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
  options,
  name,
  ...rest
}: Omit<Shared, "error"> & {
  options: ReadonlyArray<readonly [string | number, string]>;
} & Omit<SelectHTMLAttributes<HTMLSelectElement>, "id" | "className" | "children">) {
  return (
    <Wrap id={id} label={label} hint={hint} hideLabel={hideLabel}>
      <select className="select" id={id} name={name ?? id} {...rest}>
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
