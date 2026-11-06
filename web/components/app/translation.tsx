"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { NoteLanguage, Translation, TranslationBrief, TranslationLanguage } from "@/lib/api/types";

// The designer's language choices (`LANGS` in Beyric Note Taker.dc.html). English is the original.
export const LANGUAGES: { code: NoteLanguage; label: string }[] = [
  { code: "en", label: "English (original)" },
  { code: "fr", label: "French" },
  { code: "es", label: "Spanish" },
];

export const languageName = (code: NoteLanguage): string =>
  (LANGUAGES.find((l) => l.code === code) ?? LANGUAGES[0]).label.replace(" (original)", "");

// How often to ask while a translation is being made (docs/fe-integration-guide-translate.md).
export const TRANSLATION_POLL_MS = 2_000;

const isTarget = (code: NoteLanguage): code is TranslationLanguage => code !== "en";

export type TranslationView = {
  // What the select shows: the language asked for. `shown` is the one whose words are on the page.
  lang: NoteLanguage;
  shown: NoteLanguage;
  // The translation of `lang` (any state), or null while the request is going out or for English.
  current: Translation | null;
  // The shown translation's words; null when the original is shown.
  translated: Translation | null;
  // An error from asking (the limit, a lost connection), to show once.
  error: string | null;
  // The default language has no translation yet and the owner has not chosen otherwise.
  offer: TranslationLanguage | null;
  choose: (code: NoteLanguage) => void;
  showOriginal: () => void;
};

// The state of the Language select on the meeting page: nothing is translated until it is asked
// for (the select, "Translate to French", "Translate again", "Try again"); a finished translation
// of the default language is shown when the meeting opens; a translation being made is asked about
// every 2 seconds until it is ready or failed.
export function useTranslation(
  meetingId: number,
  briefs: TranslationBrief[],
  defaultLanguage: NoteLanguage,
  notes: string | null,
): TranslationView {
  // The default language, when this meeting already has (or is making) a translation of it.
  const opening = isTarget(defaultLanguage) && briefs.some((b) => b.language === defaultLanguage && b.status !== "failed");
  const [lang, setLang] = useState<NoteLanguage>(opening ? defaultLanguage : "en");
  const [shown, setShown] = useState<NoteLanguage>("en");
  const [held, setHeld] = useState<Partial<Record<TranslationLanguage, Translation>>>({});
  const [error, setError] = useState<string | null>(null);
  const [choseOriginal, setChoseOriginal] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const keep = useCallback((translation: Translation) => {
    setHeld((was) => ({ ...was, [translation.language]: translation }));
  }, []);

  // Opening in the default language when it already exists (or is being made): fetch it.
  const opened = useRef(false);
  useEffect(() => {
    if (opened.current || !opening || !isTarget(defaultLanguage)) return;
    opened.current = true;
    void api.meetings.translations
      .get(meetingId, defaultLanguage)
      .then((translation) => {
        if (!alive.current) return;
        keep(translation);
        if (translation.status === "ready") setShown(defaultLanguage);
      })
      .catch(() => {
        if (alive.current) setLang("en");
      });
  }, [defaultLanguage, keep, meetingId, opening]);

  const current = isTarget(lang) ? (held[lang] ?? null) : null;
  // Ask again every 2 seconds while it is being made.
  useEffect(() => {
    if (!isTarget(lang) || current?.status !== "translating") return;
    const timer = setInterval(() => {
      void api.meetings.translations
        .get(meetingId, lang)
        .then((translation) => {
          if (!alive.current) return;
          keep(translation);
          if (translation.status === "ready") setShown(lang);
        })
        .catch(() => {
          // Keep asking: a lost connection is not a failed translation.
        });
    }, TRANSLATION_POLL_MS);
    return () => clearInterval(timer);
  }, [current?.status, keep, lang, meetingId]);

  // The notes were rewritten: a translation held for this page may now be stale. Ask the server,
  // which compares the text it was made from.
  const firstNotes = useRef(notes);
  useEffect(() => {
    if (notes === firstNotes.current) return;
    firstNotes.current = notes;
    for (const code of Object.keys(held) as TranslationLanguage[]) {
      void api.meetings.translations
        .get(meetingId, code)
        .then((translation) => alive.current && keep(translation))
        .catch(() => {});
    }
    // `held` is read once per rewrite on purpose.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notes]);

  const choose = useCallback(
    (code: NoteLanguage) => {
      setError(null);
      if (!isTarget(code)) {
        setChoseOriginal(true);
        setLang("en");
        setShown("en");
        return;
      }
      setLang(code);
      const have = held[code];
      if (have?.status === "ready" && !have.stale) {
        setShown(code);
        return;
      }
      void api.meetings.translations
        .request(meetingId, code)
        .then((translation) => {
          if (!alive.current) return;
          keep(translation);
          if (translation.status === "ready") setShown(code);
        })
        .catch((caught) => {
          if (!alive.current) return;
          setError(errorMessage(caught));
          setLang(shown);
        });
    },
    [held, keep, meetingId, shown],
  );

  const translated = isTarget(shown) ? (held[shown] ?? null) : null;
  const offer =
    isTarget(defaultLanguage) && !choseOriginal && shown === "en" && lang === "en" && !held[defaultLanguage]
      ? defaultLanguage
      : null;
  return {
    lang,
    shown: translated?.status === "ready" ? shown : "en",
    current,
    translated: translated?.status === "ready" ? translated : null,
    error,
    offer,
    choose,
    showOriginal: () => choose("en"),
  };
}

export function Globe({ size }: { size: number }) {
  return (
    <svg
      className="lang-globe"
      width={size}
      height={size}
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

// The select on the notes toolbar: a globe, then English (original), French, Spanish.
export function LanguageSelect({
  value,
  onChange,
  disabled,
}: {
  value: NoteLanguage;
  onChange: (code: NoteLanguage) => void;
  disabled?: boolean;
}) {
  return (
    <div className="lang-select">
      <Globe size={14} />
      <select
        className="select lang-field"
        aria-label="Language"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value as NoteLanguage)}
      >
        {LANGUAGES.map((l) => (
          <option key={l.code} value={l.code}>
            {l.label}
          </option>
        ))}
      </select>
    </div>
  );
}

// The line under the toolbar: "Translating to French...", or "Translated from English to French ·
// Show original". The design has only those two; the offer, the stale line and the failed line are
// the smallest things that say what the design leaves unsaid.
export function TranslationBanner({ view }: { view: TranslationView }) {
  const { lang, current, translated, offer } = view;
  if (isTarget(lang) && (current === null || current.status === "translating")) {
    return (
      <div className="tr-working" role="status">
        Translating to {languageName(lang)}...
      </div>
    );
  }
  if (isTarget(lang) && current?.status === "failed") {
    return (
      <div className="tr-note tr-failed" role="alert">
        <span>{current.failure_message}</span>
        <button type="button" className="tr-link" onClick={() => view.choose(lang)}>
          Try again
        </button>
      </div>
    );
  }
  if (translated && isTarget(view.shown)) {
    return (
      <>
        <div className="tr-done">
          <span>Translated from English to {languageName(view.shown)}</span>
          <button type="button" className="tr-link" onClick={view.showOriginal}>
            Show original
          </button>
        </div>
        {translated.stale ? (
          <div className="tr-note">
            <span>The notes were rewritten after this was translated.</span>
            <button type="button" className="tr-link" onClick={() => view.choose(view.shown)}>
              Translate again
            </button>
          </div>
        ) : null}
      </>
    );
  }
  if (offer) {
    return (
      <div className="tr-note">
        <span>Your default notes language is {languageName(offer)}.</span>
        <button type="button" className="tr-link" onClick={() => view.choose(offer)}>
          Translate to {languageName(offer)}
        </button>
      </div>
    );
  }
  return null;
}
