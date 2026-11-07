"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { UnverifiedNotice } from "@/components/app/settings-calendar";
import { Alert, Button, Field, Icon } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import { AuthHead } from "./auth-head";

const goTo = (url: string) => window.location.assign(url);

// "Set up your workspace" (Auth.dc.html:142-169): the step label, the title, the workspace name, the
// optional calendar rows, "Continue to Beyric" and "Skip for now". `calendarConnected` is null while
// the server has the calendar off (the block is then not drawn). The Google row connects for real:
// the name, if typed, is saved first, then Google's screen opens and Google's callback returns to
// Settings with its usual message. The Outlook row is drawn off with one line (no backend yet).
export function SetupWorkspace({
  calendarConnected,
  navigate = goTo,
}: {
  calendarConnected: boolean | null;
  navigate?: (url: string) => void;
}) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState<string | undefined>();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [pendingUrl, setPendingUrl] = useState<string | null>(null);

  async function saveName(): Promise<boolean> {
    const trimmed = name.trim();
    if (!trimmed) return true;
    try {
      await api.team.update({ name: trimmed });
      return true;
    } catch (caught) {
      setError(errorMessage(caught));
      return false;
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || connecting) return;
    setError(null);
    if (!name.trim()) {
      setNameError("Give your workspace a name.");
      return;
    }
    setNameError(undefined);
    setPending(true);
    if (await saveName()) {
      router.replace("/");
      router.refresh();
      return;
    }
    setPending(false);
  }

  async function connect() {
    if (pending || connecting) return;
    setError(null);
    setConnecting(true);
    try {
      if (!(await saveName())) {
        setConnecting(false);
        return;
      }
      const start = await api.calendar.connect();
      if (start.show_unverified_notice) {
        setPendingUrl(start.authorize_url);
      } else {
        navigate(start.authorize_url);
        return; // the page is being left: keep the row disabled until it is
      }
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setConnecting(false);
  }

  return (
    <form className="auth-form" method="post" onSubmit={submit} noValidate data-screen="setup-workspace">
      <div className="setup-step">
        STEP 2 OF 2<span className="setup-step-bar" />
      </div>
      <AuthHead title="Set up your workspace">Your team, meetings and tasks live here. You can change this later.</AuthHead>
      {error ? <Alert kind="error">{error}</Alert> : null}
      <Field
        id="workspace"
        label="Workspace name"
        large
        autoFocus
        autoComplete="organization"
        placeholder="e.g. Beyric"
        maxLength={100}
        value={name}
        error={nameError}
        onChange={(event) => {
          setName(event.target.value);
          setNameError(undefined);
        }}
      />
      {calendarConnected !== null ? (
        <div className="setup-cal">
          <div>
            <div className="setup-cal-title">
              Connect your calendar <span>(optional)</span>
            </div>
            <div className="setup-cal-sub">The note taker joins upcoming meetings that have a video link.</div>
          </div>
          {pendingUrl ? (
            <UnverifiedNotice onContinue={() => navigate(pendingUrl)} />
          ) : (
            <button
              type="button"
              className={`setup-cal-row${calendarConnected ? " setup-cal-row-on" : ""}`}
              disabled={calendarConnected || connecting}
              onClick={() => void connect()}
            >
              <span className="setup-cal-tile" aria-hidden="true">
                <Icon name="calendar" />
              </span>
              <span className="setup-cal-name">Google Calendar</span>
              <span className={`setup-cal-state${calendarConnected ? " setup-cal-state-on" : ""}`}>
                {calendarConnected ? "Connected" : connecting ? "Connecting..." : "Connect"}
              </span>
            </button>
          )}
          <button type="button" className="setup-cal-row" disabled aria-describedby="setup-outlook-off">
            <span className="setup-cal-tile" aria-hidden="true">
              <Icon name="calendar" />
            </span>
            <span className="setup-cal-name">Microsoft Outlook</span>
            <span id="setup-outlook-off" className="setup-cal-state setup-cal-state-off">
              Not available yet.
            </span>
          </button>
        </div>
      ) : null}
      <div className="auth-stack-sm">
        <Button size="field" block disabled={pending || connecting}>
          {pending ? "Setting up..." : "Continue to Beyric"}
        </Button>
        <Button kind="ghost" href="/" block>
          Skip for now
        </Button>
      </div>
    </form>
  );
}
