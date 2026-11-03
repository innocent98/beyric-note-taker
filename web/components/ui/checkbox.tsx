import type { InputHTMLAttributes, ReactNode, Ref } from "react";

// The designer's checkbox: 18px, 5px radius, teal when ticked. `children` is the label (it may hold
// links); `error` replaces it with a message in the error colour under the box.
export function Checkbox({
  id,
  error,
  children,
  name,
  ref,
  ...rest
}: { id: string; error?: string; children: ReactNode; ref?: Ref<HTMLInputElement> } & Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "id" | "className" | "type" | "children"
>) {
  return (
    <div className={`check${error ? " check-error" : ""}`}>
      <div className="check-row">
        <input ref={ref} className="check-input" id={id} name={name ?? id} type="checkbox" aria-invalid={error ? true : undefined} {...rest} />
        <label className="check-label" htmlFor={id}>
          {children}
        </label>
      </div>
      {error ? <span className="field-hint">{error}</span> : null}
    </div>
  );
}
