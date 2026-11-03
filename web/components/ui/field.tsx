import type { InputHTMLAttributes, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

type Shared = { id: string; label: string; hint?: string; error?: string };

function Wrap({ id, label, hint, error, children }: Shared & { children: React.ReactNode }) {
  const note = error ?? hint;
  return (
    <div className={`field${error ? " field-error" : ""}`}>
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      {children}
      {note ? <span className="field-hint">{note}</span> : null}
    </div>
  );
}

// A labelled text input. `error` replaces the hint, in the error colour.
export function Field({
  id,
  label,
  hint,
  error,
  name,
  type = "text",
  ...rest
}: Shared & Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "className">) {
  return (
    <Wrap id={id} label={label} hint={hint} error={error}>
      <input className="input" id={id} name={name ?? id} type={type} {...rest} />
    </Wrap>
  );
}

export function SelectField({
  id,
  label,
  hint,
  options,
  name,
  ...rest
}: Omit<Shared, "error"> & {
  options: ReadonlyArray<readonly [string | number, string]>;
} & Omit<SelectHTMLAttributes<HTMLSelectElement>, "id" | "className" | "children">) {
  return (
    <Wrap id={id} label={label} hint={hint}>
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
  name,
  rows = 5,
  ...rest
}: Shared & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id" | "className">) {
  return (
    <Wrap id={id} label={label} hint={hint} error={error}>
      <textarea className="textarea" id={id} name={name ?? id} rows={rows} {...rest} />
    </Wrap>
  );
}
