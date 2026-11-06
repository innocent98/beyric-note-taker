"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Globe, LANGUAGES } from "@/components/app/translation";
import { Alert, Button, Icon } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import { validEmail } from "@/lib/accounts";
import { initials } from "@/lib/format";
import type { NoteLanguage, ShareCreated, TeamMember } from "@/lib/api/types";

export const MAX_RECIPIENTS = 10;
const AVATAR_TONES = [
  "avatar-a",
  "avatar-b",
  "avatar-c",
  "avatar-d",
  "avatar-e",
] as const;
const ATTACH: [attach: "none" | "pdf" | "docx", label: string][] = [
  ["none", "No attachment"],
  ["pdf", "PDF"],
  ["docx", "Word"],
];

// A participant is a NAME from the speaker labels. Only a member of the owner's team has an address
// the app knows (spec section 2, row 5): the name or one of the member's aliases matches.
export function emailOf(name: string, members: TeamMember[]): string | null {
  const key = name.trim().toLowerCase();
  if (!key) return null;
  const found = members.find(
    (m) =>
      m.name.trim().toLowerCase() === key ||
      m.aliases.some((a) => a.trim().toLowerCase() === key),
  );
  return found?.email ?? null;
}

// What the toast says (the designer's words: "Notes sent in French to 3 people"). A share whose
// translation is still being made says so instead (lead-designed wording, not in the designer's file).
export function sentToast(
  created: ShareCreated,
  lang: NoteLanguage,
  count: number,
): string {
  const who = `${count} ${count === 1 ? "person" : "people"}`;
  const name = LANGUAGES.find((l) => l.code === lang)?.label.replace(
    " (original)",
    "",
  );
  if (created.status === "waiting_translation")
    return `Translating the notes to ${name}. They will be sent to ${who} when ready`;
  return `Notes sent${lang !== "en" ? ` in ${name}` : ""} to ${who}`;
}

type Person = {
  name: string;
  email: string | null;
  picked: boolean;
  adding: boolean;
  typed: string;
};

// The designer's "Share notes" dialog (`shareOpen` in Beyric Note Taker.dc.html): Meeting
// participants (a tick, an avatar, a name and an address each, all ticked), Add other people (email
// chips), Language, Attach a copy (No attachment, PDF, Word), Include the full transcript, Message
// (optional), the recipient count, Cancel and Send notes.
//
// Deviations, on purpose: the language hint no longer promises "a link to the original" (the
// pilot sends no public links, decision D7; the translated mail carries the original below the
// translation), and the count shows the 10-person limit.
// LEAD-DESIGNED, not in the designer's file: a participant with no address (anyone who is not on
// the team: transcripts give names only) shows an "Add email" button where the address would be;
// it opens one input in the row, built from the dialog's own chip input. A valid address ticks the
// person and is sent with their name.
export function ShareDialog({
  open,
  meetingId,
  title,
  participants,
  members,
  hasTranscript,
  language,
  onClose,
  onShared,
}: {
  open: boolean;
  meetingId: number;
  title: string;
  participants: string[];
  members: TeamMember[];
  hasTranscript: boolean;
  // The language the page is showing: the dialog starts with it.
  language: NoteLanguage;
  onClose: () => void;
  onShared: (created: ShareCreated, lang: NoteLanguage, count: number) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [extras, setExtras] = useState<string[]>([]);
  const [typed, setTyped] = useState("");
  const [lang, setLang] = useState<NoteLanguage>(language);
  const [attach, setAttach] = useState<"none" | "pdf" | "docx">("none");
  const [transcript, setTranscript] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setPeople(
        participants.map((name) => {
          const email = emailOf(name, members);
          return {
            name,
            email,
            picked: email !== null,
            adding: false,
            typed: "",
          };
        }),
      );
      setExtras([]);
      setTyped("");
      setLang(language);
      setAttach("none");
      setTranscript(false);
      setMessage("");
      setError(null);
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    if (!open && dialog.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
    // Opening resets the form from the props of that moment.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const recipients = useMemo(() => {
    const seen = new Set<string>();
    const out: { email: string; name: string | null }[] = [];
    for (const p of people) {
      if (p.picked && p.email && !seen.has(p.email.toLowerCase())) {
        seen.add(p.email.toLowerCase());
        out.push({ email: p.email, name: p.name });
      }
    }
    for (const email of extras) {
      if (!seen.has(email)) {
        seen.add(email);
        out.push({ email, name: null });
      }
    }
    return out;
  }, [people, extras]);

  function commitExtra(raw = typed) {
    const parts = raw
      .split(/[,\s]+/)
      .map((x) => x.trim().toLowerCase())
      .filter((x) => x && validEmail(x));
    setExtras((current) => [
      ...current,
      ...parts.filter((p) => !current.includes(p)),
    ]);
    setTyped("");
  }

  function setPerson(index: number, change: Partial<Person>) {
    setPeople((current) =>
      current.map((p, i) => (i === index ? { ...p, ...change } : p)),
    );
  }

  function commitEmail(index: number) {
    const person = people[index];
    const email = person.typed.trim().toLowerCase();
    if (email && validEmail(email))
      setPerson(index, { email, picked: true, adding: false, typed: "" });
    else setPerson(index, { adding: Boolean(email), typed: email });
  }

  const count = recipients.length;
  const tooMany = count > MAX_RECIPIENTS;
  const countText =
    count === 0
      ? "Choose at least one recipient"
      : tooMany
        ? `${count} recipients: send to ${MAX_RECIPIENTS} at most`
        : `${count} ${count === 1 ? "recipient" : "recipients"}`;

  async function send() {
    if (busy || count === 0 || tooMany) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.meetings.shares.create(meetingId, {
        recipients,
        language: lang,
        attach,
        include_transcript: transcript && hasTranscript,
        message: message.trim() || null,
      });
      onShared(created, lang, count);
      onClose();
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setBusy(false);
  }

  return (
    <dialog
      ref={ref}
      className="dialog tm-dialog sh-dialog"
      aria-labelledby="sh-title"
      onClose={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      {open ? (
        <>
          <div className="sh-head">
            <div>
              <h2 className="dialog-title" id="sh-title">
                Share notes
              </h2>
              <p className="tm-dialog-sub">
                Send the notes from {title} by email.
              </p>
            </div>
            <button
              type="button"
              className="sh-close"
              aria-label="Close"
              onClick={onClose}
            >
              <Icon name="close" size={16} />
            </button>
          </div>
          <div className="tm-dialog-body">
            {people.length > 0 ? (
              <div className="field">
                <span className="field-label" id="sh-people-label">
                  Meeting participants
                </span>
                <ul className="sh-people" aria-labelledby="sh-people-label">
                  {people.map((p, index) => (
                    <li className="sh-person" key={p.name}>
                      <label className="sh-person-main">
                        <input
                          type="checkbox"
                          className="sh-box"
                          checked={p.picked}
                          disabled={p.email === null}
                          onChange={(event) =>
                            setPerson(index, { picked: event.target.checked })
                          }
                          aria-label={`Send to ${p.name}`}
                        />
                        <span
                          className={`avatar avatar-30 ${AVATAR_TONES[index % AVATAR_TONES.length]}`}
                          aria-hidden="true"
                        >
                          {initials(p.name)}
                        </span>
                        <span className="sh-person-text">
                          <span className="sh-person-name">{p.name}</span>
                          {p.email ? (
                            <span className="sh-person-email">{p.email}</span>
                          ) : null}
                        </span>
                      </label>
                      {p.email === null ? (
                        p.adding ? (
                          <input
                            className="input sh-add-input"
                            type="email"
                            autoFocus
                            aria-label={`Email address of ${p.name}`}
                            placeholder="name@company.com"
                            value={p.typed}
                            onChange={(event) =>
                              setPerson(index, { typed: event.target.value })
                            }
                            onKeyDown={(event) => {
                              if (event.key === "Enter") {
                                event.preventDefault();
                                commitEmail(index);
                              } else if (event.key === "Escape") {
                                event.stopPropagation();
                                setPerson(index, { adding: false, typed: "" });
                              }
                            }}
                            onBlur={() => commitEmail(index)}
                          />
                        ) : (
                          <button
                            type="button"
                            className="btn-link sh-add"
                            onClick={() => setPerson(index, { adding: true })}
                          >
                            Add email
                          </button>
                        )
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <div className="field">
              <label className="field-label" htmlFor="sh-other">
                Add other people
              </label>
              <div className="tm-chipbox">
                {extras.map((email) => (
                  <span className="tm-chip" key={email}>
                    {email}
                    <button
                      type="button"
                      aria-label={`Remove ${email}`}
                      onClick={() =>
                        setExtras((c) => c.filter((x) => x !== email))
                      }
                    >
                      <Icon name="close" size={11} stroke={2.6} />
                    </button>
                  </span>
                ))}
                <input
                  id="sh-other"
                  value={typed}
                  placeholder="name@company.com, press Enter"
                  autoComplete="off"
                  onChange={(event) => setTyped(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === ",") {
                      event.preventDefault();
                      commitExtra();
                    }
                  }}
                  onBlur={() => commitExtra()}
                />
              </div>
            </div>
            <div className="field">
              <label className="field-label" htmlFor="sh-lang">
                Language
              </label>
              <div className="lang-select sh-lang">
                <Globe size={15} />
                <select
                  id="sh-lang"
                  className="select sh-lang-field"
                  value={lang}
                  onChange={(event) =>
                    setLang(event.target.value as NoteLanguage)
                  }
                >
                  {LANGUAGES.map((l) => (
                    <option key={l.code} value={l.code}>
                      {l.label}
                    </option>
                  ))}
                </select>
              </div>
              <span className="field-hint">
                {lang === "en"
                  ? "Recipients get the notes as they were written."
                  : "Recipients get the notes translated, with the original below."}
              </span>
            </div>
            <div className="field">
              <span className="field-label" id="sh-attach-label">
                Attach a copy
              </span>
              <div
                className="segmented sh-attach"
                role="radiogroup"
                aria-labelledby="sh-attach-label"
              >
                {ATTACH.map(([key, label]) => (
                  <label className="segmented-opt" key={key}>
                    <input
                      type="radio"
                      name="sh-attach"
                      value={key}
                      checked={attach === key}
                      onChange={() => setAttach(key)}
                    />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
              <span className="field-hint">
                {attach === "none"
                  ? "Notes are sent in the body of the email."
                  : `Notes are sent in the email body with a ${attach === "pdf" ? "PDF" : "Word"} copy attached.`}
              </span>
            </div>
            {hasTranscript ? (
              <label className="sh-check">
                <input
                  type="checkbox"
                  className="sh-box"
                  checked={transcript}
                  onChange={(event) => setTranscript(event.target.checked)}
                />
                Include the full transcript
              </label>
            ) : null}
            <div className="field">
              <label className="field-label" htmlFor="sh-message">
                Message <span className="sh-optional">(optional)</span>
              </label>
              <textarea
                id="sh-message"
                className="textarea"
                rows={3}
                maxLength={500}
                placeholder="Add a short note for the recipients"
                value={message}
                onChange={(event) => setMessage(event.target.value)}
              />
            </div>
            {error ? <Alert kind="error">{error}</Alert> : null}
          </div>
          <div className="sh-foot">
            <span className="sh-count" role="status">
              {countText}
            </span>
            <div className="sh-foot-actions">
              <Button
                kind="secondary"
                size="md"
                type="button"
                onClick={onClose}
              >
                Cancel
              </Button>
              <Button
                size="md"
                type="button"
                disabled={busy || count === 0 || tooMany}
                onClick={() => void send()}
              >
                {busy ? "Sending..." : "Send notes"}
              </Button>
            </div>
          </div>
        </>
      ) : null}
    </dialog>
  );
}
