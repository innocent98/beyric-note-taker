"use client";

import { useState } from "react";
import { Alert, Field } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { SlackState } from "@/lib/api/types";

// The last row of Settings > Tasks, "Post tasks to Slack", live (docs/fe-integration-guide-integrations.md).
// The designer drew a "Connect Slack" button that connects in one click and a chip with the channel
// name (`#beyric-updates`) plus Disconnect. We connect with a Slack incoming-webhook address that the
// person pastes (no Slack app to install or review), and a webhook does not reveal its channel, so:
//   LEAD-DESIGNED, NOT IN THE DESIGNER'S FILE: the address field that "Connect Slack" opens under the
//   row (a label, one input, Connect and Cancel), the "Slack connected" chip text in place of the
//   channel name, the failure line, and the "Only owners and admins" note for a teammate.
// The webhook address is never shown again after it is sent: this component never holds it past the
// request.
// `personal`: this is a personal space while multi-team is on. Slack belongs to a team, so nothing can be
// connected here (the API refuses it, 409 `personal_team`); one that was connected before can still be seen
// and disconnected, with a plain line saying why it cannot be added again.
export function SettingsSlack({ initial, personal = false }: { initial: SlackState; personal?: boolean }) {
  const [state, setState] = useState(initial);
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function connect() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      setState(await api.integrations.slack.connect(url));
      setUrl("");
      setOpen(false);
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setBusy(false);
  }

  async function disconnect() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.integrations.slack.disconnect();
      setState({ ...state, connected: false, status: null, last_error: null, last_posted_at: null });
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setBusy(false);
  }

  if (personal && !state.connected) return null;
  return (
    <>
      <div className="pl-row pl-row-center pl-row-last">
        <div>
          <div className="pl-row-title">Post tasks to Slack</div>
          <div className="pl-row-sub">Share new tasks in a channel so the team can track them there.</div>
          {!state.can_manage ? <div className="pl-row-sub">Only owners and admins can change this.</div> : null}
          {personal ? <div className="pl-row-sub">This is your personal space. Slack belongs to a team, so it can be disconnected here but not connected again. Connect it from a team.</div> : null}
        </div>
        {state.connected ? (
          <div className="pl-controls">
            <span className="pl-slack-chip">{state.status === "failing" ? "Slack needs attention" : "Slack connected"}</span>
            {state.can_manage ? (
              <button type="button" className="pl-slack-disconnect" disabled={busy} onClick={() => void disconnect()}>
                Disconnect
              </button>
            ) : null}
          </div>
        ) : (
          <button
            type="button"
            className="btn btn-secondary pl-btn"
            disabled={!state.can_manage || open}
            onClick={() => setOpen(true)}
          >
            Connect Slack
          </button>
        )}
      </div>
      {open && !state.connected ? (
        <form
          className="pl-slack-form"
          onSubmit={(event) => {
            event.preventDefault();
            void connect();
          }}
        >
          <Field
            id="slack-webhook"
            label="Slack webhook address"
            hint="In Slack, add the Incoming Webhooks app to a channel and paste the address it gives you. We post one test message."
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="https://hooks.slack.com/services/..."
            value={url}
            onChange={(event) => setUrl(event.target.value)}
          />
          <div className="pl-controls">
            <button type="submit" className="btn btn-primary pl-btn" disabled={busy || !url.trim()}>
              {busy ? "Connecting..." : "Connect"}
            </button>
            <button
              type="button"
              className="btn btn-secondary pl-btn"
              disabled={busy}
              onClick={() => {
                setOpen(false);
                setUrl("");
                setError(null);
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : null}
      <div role="status" aria-live="polite">
        {state.connected && state.status === "failing" && state.last_error ? (
          <div className="set-note">
            <Alert kind="notice">{state.last_error}</Alert>
          </div>
        ) : null}
        {error ? (
          <div className="set-note">
            <Alert kind="error">{error}</Alert>
          </div>
        ) : null}
      </div>
    </>
  );
}
