"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { Alert, Button, Icon, SelectField } from "@/components/ui";
import type { AlertKind } from "@/components/ui/alert";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { AutoJoinRule, CalendarSettingsInput, CalendarState, Template } from "@/lib/api/types";
import type { CalendarNotice } from "@/lib/calendar-notice";
import { LocalTime } from "@/components/app/local-time";

type Message = { kind: AlertKind; text: string; revokeNote?: boolean };

// The words for what happened. The connect errors are the guide's (docs/fe-integration-guide-
// calendar-connect.md section 2); `different_account` names the account that is already
// connected, which only the page's own state knows.
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

// The designer's three rules (`autoRules` in Beyric Note Taker.dc.html).
const RULES = [
  ["all", "All meetings with a video link", "Every event on your calendar with a meeting link."],
  ["organizer", "Only meetings I organise", "Skip meetings other people invited you to."],
  ["none", "Only when I turn it on", "Pick meetings one by one from the Upcoming list."],
] as const;

const PERMISSIONS_URL = "https://myaccount.google.com/permissions";

// Opens Google's consent screen. A full navigation, never fetch (the connect call's contract).
const goTo = (url: string) => window.location.assign(url);

// The Calendar section of the Settings screen (the designer's, in `Beyric Note Taker.dc.html`):
// the not-connected buttons, or the connected row with Disconnect, the "Join automatically" rules
// and the "Template for calendar meetings" select. `initial` is `GET /api/calendar`, or null while
// the server has the feature off (404 `calendar_unavailable`); `templates` are the person's own, for
// the select. A rule or template is written as soon as it is chosen (`PUT /api/calendar/settings`).
// The words that come from the server (the last failure) are shown as they are.
export function SettingsCalendar({
  initial,
  notice,
  templates = [],
  navigate = goTo,
}: {
  initial: CalendarState | null;
  notice: CalendarNotice | null;
  templates?: Template[];
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
  // A rule or template being written: shown as chosen while the server answers, then replaced by
  // its answer (or dropped, with a message, when it fails).
  const [saving, setSaving] = useState<{ rule?: AutoJoinRule; template?: number } | null>(null);
  const writing = useRef(false);

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
      setState((current) => (current ? disconnectedFrom(current) : current));
      setConfirming(false);
      setMessage({ kind: "info", text: "Google Calendar disconnected.", revokeNote: !result.revoked });
    } catch (caught) {
      setError(errorMessage(caught));
    }
    busy.current = false;
    setDisconnecting(false);
  }

  async function save(body: CalendarSettingsInput, shown: { rule?: AutoJoinRule; template?: number }) {
    if (writing.current) return;
    writing.current = true;
    setSaving(shown);
    setError(null);
    try {
      setState(await api.calendar.settings(body));
    } catch (caught) {
      setError(errorMessage(caught));
    }
    writing.current = false;
    setSaving(null);
  }

  const connected = state !== null && state.state !== "not_connected";
  const needsReconnect = state?.state === "needs_reconnect";
  const connectLabel = connecting ? "Connecting..." : needsReconnect ? "Connect Google Calendar again" : "Connect Google Calendar";

  return (
    <>
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

      <section className="card card-flush" aria-labelledby="set-calendar">
        <div className="set-head">
          <h2 id="set-calendar" className="set-title">
            Calendar
          </h2>
          <p className="set-sub">
            The note taker reads your calendar and joins meetings that have a Google Meet, Microsoft Teams or Zoom
            link.
          </p>
        </div>

        {state === null ? (
          <p className="meta set-note">Google Calendar is not available yet.</p>
        ) : !connected ? (
          <Fragment>
            {pendingUrl ? (
              <div className="set-stack">
                <UnverifiedNotice onContinue={() => navigate(pendingUrl)} />
              </div>
            ) : (
              <div className="set-actions">
                <Button kind="secondary" size="tight" type="button" disabled={connecting} onClick={() => void connect()}>
                  <Icon name="calendar" />
                  {connectLabel}
                </Button>
              </div>
            )}
            <p className="meta set-note set-note-after">
              Beyric asks Google for read-only access to the events on your calendars. It never adds, changes or
              deletes anything on your calendar, keeps each event&apos;s title, time, video link and number of
              guests, and does not keep guest email addresses or event descriptions.
            </p>
          </Fragment>
        ) : (
          <div>
            <div className="cal-account">
              <div className="cal-account-main">
                <span className="cal-tile" aria-hidden="true">
                  <Icon name="calendar" size={18} />
                </span>
                <div>
                  <div className="cal-provider">Google Calendar</div>
                  <div className="cal-account-sub">
                    {state.google_email} · {needsReconnect ? "Needs to connect again" : "Connected"}
                  </div>
                </div>
              </div>
              {confirming ? null : (
                <button type="button" className="cal-text-danger" onClick={() => setConfirming(true)}>
                  Disconnect
                </button>
              )}
            </div>

            {confirming ? (
              <div className="cal-confirm" role="group" aria-label="Disconnect Google Calendar?">
                <p>
                  Beyric forgets your Google connection, deletes the calendar events it copied and cancels the
                  meetings it planned to join. Meetings already recorded and your notes stay. A meeting being
                  recorded right now carries on.
                </p>
                <div className="alert-actions">
                  <Button kind="danger" size="sm" type="button" disabled={disconnecting} onClick={() => void disconnect()}>
                    {disconnecting ? "Disconnecting..." : "Disconnect"}
                  </Button>
                  <Button kind="quiet" type="button" disabled={disconnecting} onClick={() => setConfirming(false)}>
                    Keep connected
                  </Button>
                </div>
              </div>
            ) : null}

            <div className="set-body">
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
              {pendingUrl ? <UnverifiedNotice onContinue={() => navigate(pendingUrl)} /> : null}

              <div className="cal-rules" role="radiogroup" aria-labelledby="cal-rules-label">
                <div id="cal-rules-label" className="cal-field-label">
                  Join automatically
                </div>
                {RULES.map(([code, label, description]) => {
                  const chosen = (saving?.rule ?? state.rule) === code;
                  return (
                    <label key={code} className={`cal-rule${chosen ? " cal-rule-on" : ""}`}>
                      <input
                        className="cal-rule-input"
                        type="radio"
                        name="auto-join-rule"
                        value={code}
                        checked={chosen}
                        disabled={saving !== null}
                        onChange={() => {
                          if (code !== state.rule) void save({ rule: code }, { rule: code });
                        }}
                      />
                      <span className="cal-radio" aria-hidden="true" />
                      <div>
                        <div className="cal-rule-label">{label}</div>
                        <div className="cal-rule-desc">{description}</div>
                      </div>
                    </label>
                  );
                })}
              </div>

              {templates.length > 0 ? (
                <div className="cal-template">
                  <SelectField
                    id="calendar-template"
                    label="Template for calendar meetings"
                    options={templates.map((t) => [t.id, t.name] as const)}
                    value={String(saving?.template ?? state.calendar_template_id ?? defaultTemplate(templates))}
                    disabled={saving !== null}
                    onChange={(event) => {
                      const id = Number(event.target.value);
                      if (id !== (state.calendar_template_id ?? defaultTemplate(templates))) {
                        void save({ calendar_template_id: id }, { template: id });
                      }
                    }}
                  />
                </div>
              ) : null}

              <p className="meta">
                {state.last_sync_at ? (
                  <>
                    Last updated <LocalTime iso={state.last_sync_at} />
                  </>
                ) : (
                  "Waiting for the first update. This usually takes a minute."
                )}
              </p>
            </div>
          </div>
        )}
      </section>
    </>
  );
}

// What "no template chosen" means on screen: the person's first (oldest) template, the General copy
// sign-up gave them. The list arrives sorted by name, so it is not simply the first entry.
function defaultTemplate(templates: Template[]): number {
  return templates.reduce((first, t) => (t.id < first ? t.id : first), templates[0].id);
}

// The page's state once the server has forgotten the connection (what GET would now answer).
function disconnectedFrom(current: CalendarState): CalendarState {
  return {
    connected: false,
    state: "not_connected",
    state_text: "Google Calendar is not connected.",
    google_email: null,
    last_sync_at: null,
    last_error: null,
    rule: current.rule,
    rule_text: current.rule_text,
    calendar_template_id: current.calendar_template_id,
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
