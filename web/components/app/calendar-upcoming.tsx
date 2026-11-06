"use client";

import { useRef, useState, useSyncExternalStore } from "react";
import { Alert, AppLink, Button, Icon } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { CalendarEvent, CalendarState } from "@/lib/api/types";
import { formatDuration, formatEventDay, formatEventTime, plural } from "@/lib/format";

const subscribe = () => () => {};

// The calendar on the Meetings screen (the designer's, in `Beyric Note Taker.dc.html`): while no
// calendar is connected, the "Join meetings automatically" band that leads to Settings; once one
// is, the Upcoming list. `state` is `GET /api/calendar`, or null when the server has the calendar
// off or could not answer: nothing is drawn then, so the page looks as it did before.
//
// Days and times are the viewer's own, so they are written after the page has loaded in the
// browser (the server does not know the zone). The rows keep their place until then.
//
// Each row ends with the designer's 36px switch (`u.toggle`, `u.state`, `u.hint`): on for "Will
// join", off for "Off", and dimmed with its hint for "Cannot join", which nothing the person
// switches can change. Pressing it writes the person's choice (`PUT .../override`) and the row is
// replaced by the server's answer, words included.
export function CalendarBand({
  state,
  timeZone,
  now,
}: {
  state: CalendarState | null;
  timeZone?: string;
  now?: Date;
}) {
  if (state === null) return null;
  if (state.state === "needs_reconnect") return <DisconnectedBanner />;
  if (state.state === "not_connected") return <ConnectBand />;
  return <Upcoming state={state} timeZone={timeZone} now={now} />;
}

// LEAD-DESIGNED, not in the designer's file (2026-11-05): the designer drew no "calendar
// disconnected" banner. It copies the designer's amber "tasks need attention" banner on the Tasks
// screen (same wash, line, title and secondary button), with the words the Settings card already
// uses. It shows while the connection needs a reconnect and cannot be dismissed: the designer's
// banner has no close. Reconnect leads to Settings, where the connect flow lives.
function DisconnectedBanner() {
  return (
    <section className="cal-alert" role="status" aria-label="Calendar disconnected">
      <div className="cal-alert-text">
        <div className="cal-alert-title">Your calendar is disconnected</div>
        <div className="cal-alert-sub">
          The note taker will not join meetings from your calendar until you connect again.
        </div>
      </div>
      <Button href="/settings" kind="secondary" size="md">
        Reconnect
      </Button>
    </section>
  );
}

function ConnectBand() {
  return (
    <section className="card cal-band">
      <span className="cal-band-tile" aria-hidden="true">
        <Icon name="calendar" size={20} />
      </span>
      <div className="cal-band-text">
        <div className="cal-band-title">Join meetings automatically</div>
        <div className="cal-band-sub">
          Connect your calendar and the note taker will join upcoming meetings that have a video link.
        </div>
      </div>
      <Button href="/settings" kind="secondary" size="md">
        Connect calendar
      </Button>
    </section>
  );
}

function Upcoming({ state, timeZone, now }: { state: CalendarState; timeZone?: string; now?: Date }) {
  const inBrowser = useSyncExternalStore(subscribe, () => true, () => false);
  const reference = now ?? new Date();
  // The server's rows: what the page was given, or what the last re-read gave; a row the person has
  // switched since is replaced by the answer to that switch.
  const [reread, setReread] = useState<CalendarEvent[] | null>(null);
  const [answers, setAnswers] = useState<Record<number, CalendarEvent>>({});
  // Rows whose switch is being written, with the position they were switched to.
  const [saving, setSaving] = useState<Record<number, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const writing = useRef(new Set<number>());
  const events = (reread ?? state.upcoming).map((event) => answers[event.id] ?? event);

  async function toggle(event: CalendarEvent) {
    if (event.will_join_text === "Cannot join" || writing.current.has(event.id)) return;
    const target = !event.will_join;
    writing.current.add(event.id);
    setError(null);
    setSaving((current) => ({ ...current, [event.id]: target }));
    try {
      const answer = await api.calendar.override(event.id, target);
      setAnswers((current) => ({ ...current, [event.id]: answer }));
    } catch (caught) {
      setError(errorMessage(caught));
      // The meeting changed behind the page (declined, over): read the list again so the row says so.
      if (caught instanceof ApiError && caught.status === 409) {
        try {
          const fresh = await api.calendar.get();
          setReread(fresh.upcoming);
          setAnswers({});
        } catch {
          // The row keeps its old look; the message above says why the switch did not move.
        }
      }
    }
    writing.current.delete(event.id);
    setSaving((current) => Object.fromEntries(Object.entries(current).filter(([id]) => Number(id) !== event.id)));
  }

  return (
    <section className="card card-flush" aria-labelledby="upcoming-title">
      <div className="cal-up-head">
        <div className="cal-up-head-left">
          <h2 id="upcoming-title" className="cal-up-title">
            Upcoming
          </h2>
          {state.account_email ? <span className="cal-chip">{state.account_email}</span> : null}
        </div>
        <AppLink className="cal-up-rule" href="/settings">
          {state.rule_text}
        </AppLink>
      </div>
      {state.auto_join_paused_text ? (
        <div className="cal-up-error">
          <Alert kind="notice">{state.auto_join_paused_text}</Alert>
        </div>
      ) : null}
      {error ? (
        <div className="cal-up-error">
          <Alert kind="error">{error}</Alert>
        </div>
      ) : null}
      {events.length === 0 ? (
        <p className="meta cal-up-empty">
          {state.last_sync_at ? "Nothing in the next 7 days." : "Your meetings appear here after the first update."}
        </p>
      ) : (
        <ul className="cal-up-list" aria-label="Upcoming meetings">
          {events.map((event) => (
            <Row
              key={event.id}
              event={event}
              ready={inBrowser}
              now={reference}
              timeZone={timeZone}
              switchedTo={saving[event.id]}
              onToggle={() => void toggle(event)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function Row({
  event,
  ready,
  now,
  timeZone,
  switchedTo,
  onToggle,
}: {
  event: CalendarEvent;
  ready: boolean;
  now: Date;
  timeZone?: string;
  switchedTo: boolean | undefined;
  onToggle: () => void;
}) {
  const duration = formatDuration(event.starts_at, event.ends_at);
  const cannot = event.will_join_text === "Cannot join";
  // While the write is out the switch already shows where it was pressed to, as the design's does.
  const on = switchedTo ?? event.will_join;
  const words = switchedTo === undefined ? event.will_join_text : switchedTo ? "Will join" : "Off";
  const hint = switchedTo === undefined ? event.reason_text : null;
  return (
    <li className="cal-up-row">
      <div className="cal-up-when">
        <div className="cal-up-day">{ready ? formatEventDay(event.starts_at, now, timeZone) : ""}</div>
        <div className="cal-up-time">{ready ? formatEventTime(event.starts_at, timeZone) : ""}</div>
      </div>
      <div className="cal-up-what">
        <div className="cal-up-name" id={`cal-up-name-${event.id}`}>
          {event.title}
        </div>
        <p className="meta-line">
          <span>{event.platform_label ?? "No meeting link"}</span>
          {duration ? <span>{duration}</span> : null}
          {event.attendee_count > 0 ? <span>{plural(event.attendee_count, "person", "people")}</span> : null}
        </p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-disabled={cannot ? true : undefined}
        aria-labelledby={`cal-up-name-${event.id} cal-up-state-${event.id}`}
        className={`cal-up-switch${cannot ? " cal-up-state-off" : ""}`}
        title={hint ?? undefined}
        onClick={onToggle}
      >
        <span className="cal-up-state" id={`cal-up-state-${event.id}`}>
          {words}
        </span>
        <span className={`cal-switch${on ? " cal-switch-on" : ""}`} aria-hidden="true" />
      </button>
    </li>
  );
}
