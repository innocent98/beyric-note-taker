"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { UnverifiedNotice } from "@/components/app/settings-calendar";
import { Alert, Button, Field, Icon } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { CalendarProvider } from "@/lib/api/types";
import { AuthHead } from "./auth-head";

const goTo = (url: string) => window.location.assign(url);

// "Set up your workspace" (Auth.dc.html:142-169): the step label, the title, the workspace name, the
// optional calendar rows, "Continue to Beyric" and "Skip for now". `calendarConnected` is null while
// the server has the calendar off (the block is then not drawn). The Google row connects for real:
// the name, if typed, is saved first, then Google's screen opens and Google's callback returns to
// Settings with its usual message. The Outlook row is drawn off with one line until the server says
// Outlook is on (`microsoftEnabled`), then it connects the same way. `connectedProvider` says which
// row is the connected one (one calendar at a time: the other stays disabled).
export function SetupWorkspace({
  calendarConnected,
  microsoftEnabled = false,
  connectedProvider = "google",
  navigate = goTo,
}: {
  calendarConnected: boolean | null;
  microsoftEnabled?: boolean;
  connectedProvider?: CalendarProvider | null;
  navigate?: (url: string) => void;
}) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState<string | undefined>();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [connecting, setConnecting] = useState<CalendarProvider | null>(null);
  const [consent, setConsent] = useState<{ url: string; provider: CalendarProvider } | null>(null);

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
    if (pending || connecting !== null) return;
    setError(null);
    if (!name.trim()) {
      setNameError("Give your workspace a name.");
      return;
    }
    setNameError(undefined);
    setPending(true);
    if (await saveName()) {
      router.replace("/meetings");
      router.refresh();
      return;
    }
    setPending(false);
  }

  async function connect(provider: CalendarProvider) {
    if (pending || connecting !== null) return;
    setError(null);
    setConnecting(provider);
    try {
      if (!(await saveName())) {
        setConnecting(null);
        return;
      }
      const start = await api.calendar.connect(provider);
      if (start.show_unverified_notice) {
        setConsent({ url: start.authorize_url, provider });
      } else {
        navigate(start.authorize_url);
        return; // the page is being left: keep the row disabled until it is
      }
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setConnecting(null);
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
          {consent ? (
            <UnverifiedNotice provider={consent.provider} onContinue={() => navigate(consent.url)} />
          ) : (
            <SetupRow
              name="Google Calendar"
              on={calendarConnected === true && connectedProvider === "google"}
              connecting={connecting === "google"}
              disabled={calendarConnected === true || connecting !== null}
              onClick={() => void connect("google")}
            />
          )}
          {microsoftEnabled ? (
            consent ? null : (
              <SetupRow
                name="Microsoft Outlook"
                on={calendarConnected === true && connectedProvider === "microsoft"}
                connecting={connecting === "microsoft"}
                disabled={calendarConnected === true || connecting !== null}
                onClick={() => void connect("microsoft")}
              />
            )
          ) : (
            <button type="button" className="setup-cal-row" disabled aria-describedby="setup-outlook-off">
              <span className="setup-cal-tile" aria-hidden="true">
                <Icon name="calendar" />
              </span>
              <span className="setup-cal-name">Microsoft Outlook</span>
              <span id="setup-outlook-off" className="setup-cal-state setup-cal-state-off">
                Not available yet.
              </span>
            </button>
          )}
        </div>
      ) : null}
      <div className="auth-stack-sm">
        <Button size="field" block disabled={pending || connecting !== null}>
          {pending ? "Setting up..." : "Continue to Beyric"}
        </Button>
        <Button kind="ghost" href="/meetings" block>
          Skip for now
        </Button>
      </div>
    </form>
  );
}

// One calendar row of the setup block: the designer's tile, name and the state word (`Connect`,
// `Connecting...`, `Connected`).
function SetupRow({
  name,
  on,
  connecting,
  disabled,
  onClick,
}: {
  name: string;
  on: boolean;
  connecting: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button type="button" className={`setup-cal-row${on ? " setup-cal-row-on" : ""}`} disabled={disabled} onClick={onClick}>
      <span className="setup-cal-tile" aria-hidden="true">
        <Icon name="calendar" />
      </span>
      <span className="setup-cal-name">{name}</span>
      <span className={`setup-cal-state${on ? " setup-cal-state-on" : ""}`}>
        {on ? "Connected" : connecting ? "Connecting..." : "Connect"}
      </span>
    </button>
  );
}
