"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Alert, Field, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { DisplayName } from "@/lib/api/types";

// The "Your name" card of Settings: the account's display name (`users.name`), shown to teammates and
// said by the note taker in the meeting chat when it joins ("Hi, I'm ... <name> added me ...").
//   LEAD-DESIGNED, NOT IN THE DESIGNER'S FILE: no design covers it (sign-up asks for the name, but
//   there was no screen to change it, and the owner's account has none). The card reuses the Time
//   zone / Note taker name card (`card pl-language`), the standard `Field` and the primary
//   `pl-btn` button, and invents no style. The label and the help text are the owner's wording.
// There is no Clear: an empty name is refused by the server (`name_invalid`, a plain message shown
// under the field), so once a person has a name it stays one. After a save the server render is
// asked again so the name shows wherever the page uses it.
export function SettingsYourName({ initial }: { initial: DisplayName }) {
  const router = useRouter();
  const [saved, setSaved] = useState<string>(initial.name ?? "");
  const [value, setValue] = useState<string>(initial.name ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, flash] = useToast();
  const latest = useRef(0);

  async function save(next: string) {
    if (busy) return;
    const mine = ++latest.current;
    setBusy(true);
    setError(null);
    try {
      const stored = await api.settings.name.set(next);
      if (latest.current !== mine) return;
      const text = stored.name ?? "";
      setSaved(text);
      setValue(text);
      flash("Name saved");
      router.refresh();
    } catch (caught) {
      // The 422 text of this route is written for the person (the shared helper keeps other 422
      // texts back as developer wording), so it is shown as it comes.
      const refused = caught instanceof ApiError && caught.code === "name_invalid";
      if (latest.current === mine) setError(refused ? caught.message : errorMessage(caught));
    } finally {
      if (latest.current === mine) setBusy(false);
    }
  }

  return (
    <section className="card pl-language" aria-labelledby="set-your-name">
      <div className="pl-language-text">
        <h2 id="set-your-name" className="set-title">
          Your name
        </h2>
        <p className="set-sub">
          This is the name shown to your teammates and in the message the note taker posts when it joins your meetings.
        </p>
      </div>
      <form
        className="pl-language-control"
        onSubmit={(event) => {
          event.preventDefault();
          void save(value);
        }}
      >
        <Field
          id="your-name"
          label="Your name"
          hideLabel
          autoComplete="name"
          maxLength={200}
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
        <div className="pl-controls">
          <button type="submit" className="btn btn-primary pl-btn" disabled={busy || value === saved}>
            {busy ? "Saving..." : "Save"}
          </button>
        </div>
        {error ? <Alert kind="error">{error}</Alert> : null}
      </form>
      <Toast message={toast} />
    </section>
  );
}
