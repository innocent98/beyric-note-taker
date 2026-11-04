"use client";

import { useSyncExternalStore } from "react";
import { AppLink, Button, Icon } from "@/components/ui";
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
  if (state.state === "not_connected" || state.state === "needs_reconnect") return <ConnectBand />;
  return <Upcoming state={state} timeZone={timeZone} now={now} />;
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
  return (
    <section className="card card-flush" aria-labelledby="upcoming-title">
      <div className="cal-up-head">
        <div className="cal-up-head-left">
          <h2 id="upcoming-title" className="cal-up-title">
            Upcoming
          </h2>
          {state.google_email ? <span className="cal-chip">{state.google_email}</span> : null}
        </div>
        <AppLink className="cal-up-rule" href="/settings">
          {state.rule_text}
        </AppLink>
      </div>
      {state.upcoming.length === 0 ? (
        <p className="meta cal-up-empty">
          {state.last_sync_at ? "Nothing in the next 7 days." : "Your meetings appear here after the first update."}
        </p>
      ) : (
        <ul className="cal-up-list" aria-label="Upcoming meetings">
          {state.upcoming.map((event) => (
            <Row key={event.id} event={event} ready={inBrowser} now={reference} timeZone={timeZone} />
          ))}
        </ul>
      )}
    </section>
  );
}

function Row({ event, ready, now, timeZone }: { event: CalendarEvent; ready: boolean; now: Date; timeZone?: string }) {
  const duration = formatDuration(event.starts_at, event.ends_at);
  const cannot = event.will_join_text === "Cannot join";
  return (
    <li className="cal-up-row">
      <div className="cal-up-when">
        <div className="cal-up-day">{ready ? formatEventDay(event.starts_at, now, timeZone) : ""}</div>
        <div className="cal-up-time">{ready ? formatEventTime(event.starts_at, timeZone) : ""}</div>
      </div>
      <div className="cal-up-what">
        <div className="cal-up-name">{event.title}</div>
        <p className="meta-line">
          <span>{event.platform_label ?? "No meeting link"}</span>
          {duration ? <span>{duration}</span> : null}
          {event.attendee_count > 0 ? <span>{plural(event.attendee_count, "person", "people")}</span> : null}
        </p>
      </div>
      <div
        className={`cal-up-state${cannot ? " cal-up-state-off" : ""}`}
        title={event.reason_text ?? undefined}
      >
        {event.will_join_text}
      </div>
    </li>
  );
}
