"use client";

import { useEffect, useRef, useState } from "react";
import { Alert, Badge, Button, Icon } from "@/components/ui";
import type { AlertKind } from "@/components/ui/alert";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { CalendarEvent, CalendarState } from "@/lib/api/types";
import type { CalendarNotice } from "@/lib/calendar-notice";
import { formatClock, formatDuration, formatShortDay, formatTime } from "@/lib/format";

type Message = { kind: AlertKind; text: string; revokeNote?: boolean };

// The words for what happened. The connect errors are the guide's; `different_account` names the
// account that is already connected, which only the page's own state knows.
function messageFor(notice: CalendarNotice, email: string | null): Message {
  if (notice.kind === "connected") return { kind: "info", text: "Google Calendar connected." };
  switch (notice.code) {
    case "google_denied":
      return { kind: "info", text: "You did not connect your calendar." };
    case "scope_missing":
      return {
        kind: "error",
        text: "To join your meetings automatically we need permission to read your calendar events. Try again and leave the calendar box ticked.",
      };
    case "no_refresh_token":
      return {
        kind: "error",
        text: "Google did not finish connecting. Remove Beyric at myaccount.google.com/permissions and try again.",
      };
    case "different_account":
      return {
        kind: "notice",
        text: `A different Google account is already connected${email ? ` (${email})` : ""}. To use another one, disconnect first.`,
      };
    case "wrong_account":
      return {
        kind: "error",
        text: "This connection was started from a different account. Sign in with the right account and start again.",
      };
    default:
      return { kind: "error", text: "Something went wrong connecting Google. Try again." };
  }
}

const PERMISSIONS_URL = "https://myaccount.google.com/permissions";

// Opens Google's consent screen. A full navigation, never fetch (the connect call's contract).
const goTo = (url: string) => window.location.assign(url);

// The Settings screen's Calendar section (the designer's Settings screen, calendar part only) and
// the Upcoming list. `initial` is `GET /api/calendar`, or null while the server has the feature off
// (404 `calendar_unavailable`). Everything on the page is the server's words (docs/fe-integration-
// guide-calendar-settings.md): the state sentence, the last failure and each meeting's answer.
export function CalendarSettings({
  initial,
  notice,
  navigate = goTo,
}: {
  initial: CalendarState | null;
  notice: CalendarNotice | null;
  navigate?: (url: string) => void;
}) {
  const [state, setState] = useState<CalendarState | null>(initial);
  const [message, setMessage] = useState<Message | null>(() =>
    notice ? messageFor(notice, initial?.google_email ?? null) : null,
  );
  const [connecting, setConnecting] = useState(false);
  // Google's unverified-app warning is explained once the connect call has answered, before leaving.
  const [pendingUrl, setPendingUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const busy = useRef(false);

  // The redirect's query has been read; drop it so a reload does not show the message again.
  useEffect(() => {
    if (!notice) return;
    try {
      window.history.replaceState(window.history.state, "", window.location.pathname);
    } catch {
      // A browser that refuses is only left with the query in the address bar.
    }
  }, [notice]);

  async function connect() {
    if (busy.current) return;
    busy.current = true;
    setConnecting(true);
    setError(null);
    setMessage(null);
    try {
      const start = await api.calendar.connect();
      if (start.show_unverified_notice) {
        setPendingUrl(start.authorize_url);
      } else {
        navigate(start.authorize_url);
        return; // the page is being left: keep the button disabled until it is
      }
    } catch (caught) {
      setError(errorMessage(caught));
    }
    busy.current = false;
    setConnecting(false);
  }

  async function disconnect() {
    if (busy.current) return;
    busy.current = true;
    setDisconnecting(true);
    setError(null);
    try {
      const result = await api.calendar.disconnect();
      setState(disconnectedState());
      setConfirming(false);
      setMessage({ kind: "info", text: "Google Calendar disconnected.", revokeNote: !result.revoked });
    } catch (caught) {
      setError(errorMessage(caught));
    }
    busy.current = false;
    setDisconnecting(false);
  }

  const connected = state !== null && state.state !== "not_connected";
  const needsReconnect = state?.state === "needs_reconnect";
  const connectLabel = connecting ? "Connecting..." : needsReconnect ? "Connect Google Calendar again" : "Connect Google Calendar";

  return (
    <div className="stack-md measure">
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>Preferences for your notes and transcripts.</p>
        </div>
      </div>

      {message ? (
        <Alert kind={message.kind}>
          <p className="alert-text">{message.text}</p>
          {message.revokeNote ? (
            <p className="alert-text">
              Beyric could not ask Google to remove its access. To be sure, remove Beyric at{" "}
              <a href={PERMISSIONS_URL} target="_blank" rel="noreferrer">
                myaccount.google.com/permissions
              </a>
              .
            </p>
          ) : null}
        </Alert>
      ) : null}
      {error ? <Alert kind="error">{error}</Alert> : null}

      <section className="card card-flush" aria-labelledby="cal-title">
        <div className="cal-head">
          <h2 id="cal-title" className="cal-title">
            Calendar
          </h2>
          <p className="cal-sub">
            Beyric reads your calendar and joins meetings that have a Google Meet or Zoom link.
          </p>
        </div>

        {state === null ? (
          <p className="cal-pad meta">Google Calendar is not available yet.</p>
        ) : !connected ? (
          <div className="cal-pad stack-sm">
            <PermissionsNote />
            {pendingUrl ? (
              <div className="stack-sm">
                <UnverifiedNotice onContinue={() => navigate(pendingUrl)} />
              </div>
            ) : (
              <div>
                <Button kind="secondary" type="button" disabled={connecting} onClick={() => void connect()}>
                  <Icon name="calendar" />
                  {connectLabel}
                </Button>
              </div>
            )}
          </div>
        ) : (
          <div className="cal-pad stack-sm">
            <div className="cal-account">
              <div className="cal-account-main">
                <span className="cal-tile" aria-hidden="true">
                  <Icon name="calendar" size={18} />
                </span>
                <div className="cal-account-text">
                  <div className="cal-provider">Google Calendar</div>
                  <div className="cal-email">{state.google_email}</div>
                </div>
                {state.state === "connected" ? (
                  <Badge tone="success" dot>
                    {state.state_text}
                  </Badge>
                ) : null}
              </div>
              {confirming ? null : (
                <Button kind="quiet" type="button" onClick={() => setConfirming(true)}>
                  Disconnect
                </Button>
              )}
            </div>

            {confirming ? (
              <div className="cal-confirm" role="group" aria-label="Disconnect Google Calendar?">
                <p className="cal-confirm-text">
                  Beyric forgets your Google connection, deletes the calendar events it copied and cancels the
                  meetings it planned to join. Meetings already recorded and your notes stay. A meeting being
                  recorded right now carries on.
                </p>
                <div className="alert-actions">
                  <Button
                    kind="danger"
                    size="sm"
                    type="button"
                    disabled={disconnecting}
                    onClick={() => void disconnect()}
                  >
                    {disconnecting ? "Disconnecting..." : "Disconnect"}
                  </Button>
                  <Button kind="quiet" type="button" disabled={disconnecting} onClick={() => setConfirming(false)}>
                    Keep connected
                  </Button>
                </div>
              </div>
            ) : null}

            {needsReconnect ? (
              <Alert kind="notice">
                <p className="alert-text">{state.state_text}</p>
                <Button kind="secondary" size="sm" type="button" disabled={connecting} onClick={() => void connect()}>
                  {connectLabel}
                </Button>
              </Alert>
            ) : (
              <>
                {state.state === "degraded" ? <p className="meta">{state.state_text}</p> : null}
                {state.last_error ? <Alert kind="notice">{state.last_error}</Alert> : null}
              </>
            )}
            {pendingUrl ? (
              <UnverifiedNotice onContinue={() => navigate(pendingUrl)} />
            ) : null}

            <p className="meta">
              {state.last_sync_at
                ? `Last updated ${formatTime(state.last_sync_at)}`
                : "Waiting for the first update. This usually takes a minute."}
            </p>
          </div>
        )}
      </section>

      {connected && state ? <Upcoming state={state} /> : null}
    </div>
  );
}

// The page's state once the server has forgotten the connection (what GET would now answer).
function disconnectedState(): CalendarState {
  return {
    connected: false,
    state: "not_connected",
    state_text: "Google Calendar is not connected.",
    google_email: null,
    last_sync_at: null,
    last_error: null,
    upcoming: [],
  };
}

// Shown after the connect call answered with `show_unverified_notice`, before Google's screen.
// Not seen live: the wording of Google's own warning is described, not quoted.
function UnverifiedNotice({ onContinue }: { onContinue: () => void }) {
  return (
    <Alert kind="notice">
      <p className="alert-text">
        Google will warn that it has not verified Beyric yet. That is expected for now. On the warning screen choose
        Advanced, then continue to Beyric.
      </p>
      <Button type="button" size="sm" onClick={onContinue}>
        Continue to Google
      </Button>
    </Alert>
  );
}

function PermissionsNote() {
  return (
    <div className="cal-perms">
      <div className="cal-perms-title">What Google will be asked for</div>
      <ul className="cal-perms-list">
        <li>See the events on your calendars. This is read only: Beyric never adds, changes or deletes anything on your calendar.</li>
      </ul>
      <p className="meta">
        Beyric keeps each event&apos;s title, time and video link, and how many people are invited. It does not keep
        guest email addresses or event descriptions. You can disconnect at any time.
      </p>
    </div>
  );
}

function Upcoming({ state }: { state: CalendarState }) {
  const events = state.upcoming;
  const anyJoin = events.some((e) => e.will_join);
  return (
    <section className="card card-flush" aria-labelledby="up-title">
      <div className="cal-up-head">
        <h2 id="up-title" className="cal-title">
          Upcoming
        </h2>
        {state.google_email ? <span className="cal-chip">{state.google_email}</span> : null}
      </div>
      {events.length === 0 ? (
        <p className="cal-pad meta">
          {state.last_sync_at ? "Nothing in the next 7 days." : "Your meetings appear here after the first update."}
        </p>
      ) : (
        <ul className="cal-up-list" aria-label="Upcoming meetings">
          {events.map((event) => (
            <UpcomingRow key={event.id} event={event} />
          ))}
        </ul>
      )}
      {anyJoin ? (
        <p className="cal-remind">
          <Icon name="info" />
          Tell participants that this meeting will be recorded.
        </p>
      ) : null}
    </section>
  );
}

function UpcomingRow({ event }: { event: CalendarEvent }) {
  const duration = formatDuration(event.starts_at, event.ends_at);
  return (
    <li className="cal-up-row">
      <div className="cal-up-when">
        <div className="cal-up-day">{formatShortDay(event.starts_at)}</div>
        <div className="cal-up-time">{formatClock(event.starts_at)}</div>
      </div>
      <div className="cal-up-what">
        <div className="cal-up-title">{event.title}</div>
        <p className="meta-line">
          {event.platform_label ? <span>{event.platform_label}</span> : null}
          {duration ? <span>{duration}</span> : null}
        </p>
      </div>
      <Badge tone={event.will_join ? "success" : "neutral"}>{event.will_join_text}</Badge>
    </li>
  );
}
