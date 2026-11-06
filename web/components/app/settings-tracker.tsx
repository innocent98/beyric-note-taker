"use client";

import { useState } from "react";
import { Alert, Field } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { LinearState, LinearTeam } from "@/lib/api/types";

// The "Sync with your project tool" row of Settings > Tasks, live for Linear (the one tracker built;
// docs/fe-integration-guide-integrations.md). The designer drew a select (Jira, Linear, Asana, Taiga)
// with a Connect button that connects in one click, and a green "Syncing with <tool>" pill with
// Disconnect. Kept as drawn; the other three options are listed but cannot be chosen yet.
//   LEAD-DESIGNED, NOT IN THE DESIGNER'S FILE: the form that "Connect" opens under the row (a
//   personal API key field, "Find teams", a Linear team select, Connect, Cancel), the "Only Linear
//   is available for now." line, the failing pill and its line, and the teammate note.
// The key is sent once and never held past the request; the state never contains it.
const TOOLS = ["Jira", "Linear", "Asana", "Taiga"] as const;

export function SettingsTracker({ initial }: { initial: LinearState }) {
  const [state, setState] = useState(initial);
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const [teams, setTeams] = useState<LinearTeam[] | null>(null);
  const [teamId, setTeamId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function close() {
    setOpen(false);
    setKey("");
    setTeams(null);
    setTeamId("");
    setError(null);
  }

  async function findTeams() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const found = (await api.integrations.linear.teams(key)).teams;
      setTeams(found);
      setTeamId(found[0]?.id ?? "");
      if (found.length === 0) setError("That key cannot reach any Linear team.");
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setBusy(false);
  }

  async function connect() {
    if (busy || !teamId) return;
    setBusy(true);
    setError(null);
    try {
      setState(await api.integrations.linear.connect(key, teamId));
      close();
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
      await api.integrations.linear.disconnect();
      setState({ ...state, connected: false, status: null, last_error: null, last_synced_at: null, team_name: null });
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setBusy(false);
  }

  return (
    <>
      <div className="pl-row pl-row-center">
        <div>
          <div className="pl-row-title">Sync with your project tool</div>
          <div className="pl-row-sub">Send tasks, projects and sprints to a tool your team already uses.</div>
          <div className="pl-row-sub">Only Linear is available for now. New tasks from meetings shared with the team become issues.</div>
          {!state.can_manage ? <div className="pl-row-sub">Only owners and admins can change this.</div> : null}
        </div>
        {state.connected ? (
          <div className="pl-controls">
            <span className={state.status === "failing" ? "pl-slack-chip" : "pl-sync-pill"}>
              {state.status === "failing" ? "Linear needs attention" : `Syncing with Linear${state.team_name ? ` · ${state.team_name}` : ""}`}
            </span>
            {state.can_manage ? (
              <button type="button" className="pl-slack-disconnect" disabled={busy} onClick={() => void disconnect()}>
                Disconnect
              </button>
            ) : null}
          </div>
        ) : (
          <div className="pl-controls">
            <select className="select pl-s34" aria-label="Project tool" defaultValue="Linear">
              {TOOLS.map((tool) => (
                <option key={tool} value={tool} disabled={tool !== "Linear"}>
                  {tool}
                </option>
              ))}
            </select>
            <button type="button" className="btn btn-secondary pl-btn" disabled={!state.can_manage || open} onClick={() => setOpen(true)}>
              Connect
            </button>
          </div>
        )}
      </div>
      {open && !state.connected ? (
        <form
          className="pl-slack-form"
          onSubmit={(event) => {
            event.preventDefault();
            void (teams ? connect() : findTeams());
          }}
        >
          <Field
            id="linear-key"
            label="Linear personal API key"
            hint="In Linear, open Settings, Security and access, and create a personal API key. We use it only to create issues."
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="lin_api_..."
            value={key}
            disabled={teams !== null}
            onChange={(event) => setKey(event.target.value)}
          />
          {teams && teams.length > 0 ? (
            <div className="field">
              <label className="field-label" htmlFor="linear-team">
                Linear team
              </label>
              <select id="linear-team" className="select" value={teamId} onChange={(event) => setTeamId(event.target.value)}>
                {teams.map((team) => (
                  <option key={team.id} value={team.id}>
                    {team.name} ({team.key})
                  </option>
                ))}
              </select>
            </div>
          ) : null}
          <div className="pl-controls">
            <button type="submit" className="btn btn-primary pl-btn" disabled={busy || (teams ? !teamId : !key.trim())}>
              {busy ? "Checking..." : teams ? "Connect" : "Find teams"}
            </button>
            <button type="button" className="btn btn-secondary pl-btn" disabled={busy} onClick={close}>
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
