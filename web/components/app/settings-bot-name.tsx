"use client";

import { useRef, useState } from "react";
import { Alert, Field, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { BotNamePreferences } from "@/lib/api/types";

// The "Note taker name" card of Settings: the name people see when the note taker joins this
// person's meetings (docs/sop/2026-11-14-per-user-bot-name.md).
//   LEAD-DESIGNED, NOT IN THE DESIGNER'S FILE: no design covers it. The card reuses the Time zone /
//   Default notes language card (`card pl-language`), the standard `Field` and the standard
//   secondary and primary buttons, and invents no style. The label, the help text and the example
//   are the owner's approved wording. Two lines the lead added after review (also lead-designed):
//   "On Google Meet the note taker always shows its own name, “<account name>”. Your name shows on
//   Zoom and Teams." under the help text, true by live evidence (2026-11-14, a real Meet call: the
//   signed-in bot showed the Google account's own name, not the configured one); the account name
//   is `meet_account_name` from the server and the line is left out when it is null (no signed-in
//   account, no account name set, or the server switch is on). And the soft `warning` (a notice,
//   never an error) under the field for a name that sounds like a recorder. The warning comes from
//   the server; a save is never blocked by it.
// The server default is read from the server (`bot_name_default`) and shown in the help text and as
// the placeholder; it is never written in this file. An empty value is the default: "Clear" and
// "Save" with an empty field both send null. The server checks the name (50 characters, no hidden
// characters, no email or web address) and answers a plain message that is shown under the field.
export function SettingsBotName({ initial }: { initial: BotNamePreferences }) {
  const [warning, setWarning] = useState<string | null>(initial.warning ?? null);
  const [saved, setSaved] = useState<string>(initial.bot_name ?? "");
  const [value, setValue] = useState<string>(initial.bot_name ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, flash] = useToast();
  const latest = useRef(0);
  const fallback = initial.bot_name_default;

  async function save(next: string, message: string) {
    if (busy) return;
    const mine = ++latest.current;
    setBusy(true);
    setError(null);
    try {
      const stored = await api.settings.botName.set(next.trim() === "" ? null : next);
      if (latest.current !== mine) return;
      const text = stored.bot_name ?? "";
      setSaved(text);
      setValue(text);
      setWarning(stored.warning ?? null);
      flash(message);
    } catch (caught) {
      // The 422 text of this route is written for the person (the shared helper keeps other 422
      // texts back as developer wording), so it is shown as it comes.
      const refused = caught instanceof ApiError && caught.code === "bot_name_invalid";
      if (latest.current === mine) setError(refused ? caught.message : errorMessage(caught));
    } finally {
      if (latest.current === mine) setBusy(false);
    }
  }

  return (
    <section className="card pl-language" aria-labelledby="set-bot-name">
      <div className="pl-language-text">
        <h2 id="set-bot-name" className="set-title">
          Note taker name
        </h2>
        <p className="set-sub">
          {
            "This is the name people see when the note taker joins your meetings. Leave it empty to use “"
          }
          {fallback}
          {
            "”. Choose a name your attendees will recognise as your notes taker, such as “Alex from Product”. Don't use another person's name, and don't hide that it records. Google Meet and Zoom may refuse bots whose names look like recorders."
          }
        </p>
        {initial.meet_account_name ? (
          <p className="set-sub">
            {"On Google Meet the note taker always shows its own name, “"}
            {initial.meet_account_name}
            {"”. Your name shows on Zoom and Teams."}
          </p>
        ) : null}
      </div>
      <form
        className="pl-language-control"
        onSubmit={(event) => {
          event.preventDefault();
          void save(value, "Note taker name saved");
        }}
      >
        <Field
          id="bot-name"
          label="Note taker name"
          hideLabel
          autoComplete="off"
          spellCheck={false}
          maxLength={200}
          placeholder={fallback}
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
        <div className="pl-controls">
          <button type="submit" className="btn btn-primary pl-btn" disabled={busy || value === saved}>
            {busy ? "Saving..." : "Save"}
          </button>
          <button
            type="button"
            className="btn btn-secondary pl-btn"
            disabled={busy || (saved === "" && value === "")}
            onClick={() => void save("", "Note taker name cleared")}
          >
            Clear
          </button>
        </div>
        {error ? <Alert kind="error">{error}</Alert> : null}
        {warning && !error ? <Alert kind="notice">{warning}</Alert> : null}
      </form>
      <Toast message={toast} />
    </section>
  );
}
