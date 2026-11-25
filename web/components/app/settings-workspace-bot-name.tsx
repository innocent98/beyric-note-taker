"use client";

import { useRef, useState } from "react";
import { Alert, Field, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { WorkspacePrefs } from "@/lib/api/types";

// The "Note taker name in this workspace" card of Settings > This workspace (workspaces design,
// section 3): the name the note taker joins THIS workspace's meetings with. Left empty, the person's
// own default from Settings > Personal applies (`bot_name_default`, read from the server).
//   LEAD-DESIGNED, NOT IN THE DESIGNER'S FILE: it is the "Note taker name" card (settings-bot-name.tsx)
//   with the same classes, `Field` and buttons, and a different title and help text that name the
//   workspace. Nothing else is invented. A save with an empty field sends null (clears it).
export function SettingsWorkspaceBotName({ initial, workspaceName }: { initial: WorkspacePrefs; workspaceName: string }) {
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
      const stored = await api.workspacePrefs.put({ bot_name: next.trim() === "" ? null : next });
      if (latest.current !== mine) return;
      const text = stored.bot_name ?? "";
      setSaved(text);
      setValue(text);
      setWarning(stored.warning ?? null);
      flash(message);
    } catch (caught) {
      const refused = caught instanceof ApiError && caught.code === "bot_name_invalid";
      if (latest.current === mine) setError(refused ? caught.message : errorMessage(caught));
    } finally {
      if (latest.current === mine) setBusy(false);
    }
  }

  return (
    <section className="card pl-language" aria-labelledby="set-ws-bot-name">
      <div className="pl-language-text">
        <h2 id="set-ws-bot-name" className="set-title">
          Note taker name in this workspace
        </h2>
        <p className="set-sub">
          {`The name people see when the note taker joins your meetings in ${workspaceName}. Leave it empty to use “`}
          {fallback}
          {"”, your default from Personal settings."}
        </p>
      </div>
      <form
        className="pl-language-control"
        onSubmit={(event) => {
          event.preventDefault();
          void save(value, "Note taker name saved");
        }}
      >
        <Field
          id="workspace-bot-name"
          label="Note taker name in this workspace"
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
