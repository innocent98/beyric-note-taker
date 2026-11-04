"use client";

import { useRef, useState } from "react";
import { Alert, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { NoteLanguage } from "@/lib/api/types";

// The "Default notes language" card of the designer's Settings (`defaultLangs` in
// Beyric Note Taker.dc.html): the language meetings open in when a translation exists. Choosing one
// is saved at once and answered with the designer's toast; it translates nothing by itself.
const CHOICES: ReadonlyArray<readonly [NoteLanguage, string]> = [
  ["en", "Original (no translation)"],
  ["fr", "French"],
  ["es", "Spanish"],
];

function Globe() {
  return (
    <svg
      className="pl-globe"
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
    </svg>
  );
}

export function SettingsLanguage({ initial }: { initial: NoteLanguage }) {
  const [value, setValue] = useState<NoteLanguage>(initial);
  const [error, setError] = useState<string | null>(null);
  const [toast, flash] = useToast();
  const latest = useRef(0);

  async function change(next: NoteLanguage) {
    const before = value;
    const mine = ++latest.current;
    setValue(next);
    setError(null);
    try {
      await api.settings.language.set(next);
      if (latest.current === mine) flash("Default notes language updated");
    } catch (caught) {
      if (latest.current !== mine) return;
      setValue(before);
      setError(errorMessage(caught));
    }
  }

  return (
    <section className="card pl-language" aria-labelledby="set-language">
      <div className="pl-language-text">
        <h2 id="set-language" className="set-title">
          Default notes language
        </h2>
        <p className="set-sub">
          Notes and transcripts open in this language. You can switch back to the original on any meeting.
        </p>
      </div>
      <div className="pl-language-control">
        <div className="pl-language-select">
          <Globe />
          <select
            className="select pl-lang"
            aria-label="Default notes language"
            value={value}
            onChange={(event) => void change(event.target.value as NoteLanguage)}
          >
            {CHOICES.map(([code, label]) => (
              <option key={code} value={code}>
                {label}
              </option>
            ))}
          </select>
        </div>
        {error ? <Alert kind="error">{error}</Alert> : null}
      </div>
      <Toast message={toast} />
    </section>
  );
}
