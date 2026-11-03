"use client";

import { Button, Field, Icon } from "@/components/ui";
import { PASSWORD_MAX, PASSWORD_MIN } from "@/lib/accounts";

type Props = {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  show: boolean;
  // The Show / Hide button. Left out on a second field that follows the first one's toggle.
  onToggle?: () => void;
  autoComplete: "current-password" | "new-password";
  placeholder?: string;
  error?: string;
  hint?: string;
  autoFocus?: boolean;
};

// A password with the designer's Show / Hide button inside its right edge. The caller holds `show`,
// so a form with two fields (new password, confirm) toggles both from one button.
export function PasswordField({ id, label, value, onChange, show, onToggle, autoComplete, placeholder, error, hint, autoFocus }: Props) {
  return (
    <Field
      id={id}
      label={label}
      type={show ? "text" : "password"}
      large
      autoFocus={autoFocus}
      autoComplete={autoComplete}
      placeholder={placeholder}
      value={value}
      error={error}
      hint={hint}
      onChange={(event) => onChange(event.target.value)}
      end={
        onToggle ? (
          <Button kind="quiet" type="button" onClick={onToggle} aria-pressed={show}>
            {show ? "Hide" : "Show"}
          </Button>
        ) : undefined
      }
    />
  );
}

// The one rule the screen can check as the person types. The server also refuses common passwords
// (it alone has the list), so the line says so rather than drawing rules that do not exist.
export function PasswordRules({ password }: { password: string }) {
  const ok = password.length >= PASSWORD_MIN && password.length <= PASSWORD_MAX;
  return (
    <div className="pw-rules">
      <span className={`pw-rule${ok ? " pw-rule-ok" : ""}`}>
        <Icon name="check" size={12} stroke={3} />
        {PASSWORD_MIN} to {PASSWORD_MAX} characters
      </span>
      <span className="pw-rule">Not a common password</span>
    </div>
  );
}
