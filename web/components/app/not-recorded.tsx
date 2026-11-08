"use client";

import { useSyncExternalStore } from "react";
import { Button } from "@/components/ui";
import type { NotRecordedItem } from "@/lib/api/types";
import { formatDuration, formatEventDay, formatEventTime } from "@/lib/format";
import type { SendNowControl } from "./use-send-now";

const subscribe = () => () => {};

// "Not recorded" on the Meetings screen: the last seven days' meetings the note taker did not get
// into, each with the reason in the server's words and a Send now while the meeting is still on
// (`GET /api/not-recorded`, docs/fe-integration-guide-calendar-send-now.md).
//
// LEAD-DESIGNED, not in the designer's file (2026-11-08): `Beyric Note Taker.dc.html` draws no
// "Not recorded" rows and no Send now. It is the designer's Upcoming card (head with title and chip,
// the same 76px / name / action row, the same day and time words) with the switch replaced by a
// secondary small button, as the other secondary actions of the app are. Nothing is drawn while there
// is nothing to show. Days and times are the viewer's own, written after the page has loaded in the
// browser, as the Upcoming list does.
export function NotRecorded({
  items,
  control,
  timeZone,
  now,
}: {
  items: NotRecordedItem[];
  control: SendNowControl;
  timeZone?: string;
  now?: Date;
}) {
  const inBrowser = useSyncExternalStore(subscribe, () => true, () => false);
  const reference = now ?? new Date();
  const shown = items.filter((item) => !control.sent.has(item.join_id));
  if (shown.length === 0) return null;
  return (
    <section className="card card-flush" aria-labelledby="not-recorded-title">
      <div className="cal-up-head">
        <div className="cal-up-head-left">
          <h2 id="not-recorded-title" className="cal-up-title">
            Not recorded
          </h2>
          <span className="cal-chip">Last 7 days</span>
        </div>
      </div>
      <ul className="cal-up-list" aria-label="Meetings not recorded">
        {shown.map((item) => (
          <Row key={item.join_id} item={item} control={control} ready={inBrowser} now={reference} timeZone={timeZone} />
        ))}
      </ul>
    </section>
  );
}

function Row({
  item,
  control,
  ready,
  now,
  timeZone,
}: {
  item: NotRecordedItem;
  control: SendNowControl;
  ready: boolean;
  now: Date;
  timeZone?: string;
}) {
  const duration = formatDuration(item.starts_at, item.ends_at);
  const busy = control.sending.has(item.join_id);
  const error = control.errors[item.join_id];
  return (
    <li className="cal-up-row nr-row">
      <div className="cal-up-when">
        <div className="cal-up-day">{ready ? formatEventDay(item.starts_at, now, timeZone) : ""}</div>
        <div className="cal-up-time">{ready ? formatEventTime(item.starts_at, timeZone) : ""}</div>
      </div>
      <div className="cal-up-what">
        <div className="cal-up-name" id={`nr-name-${item.join_id}`}>
          {item.title}
        </div>
        <p className="meta-line">
          <span>{item.platform_label}</span>
          {duration ? <span>{duration}</span> : null}
        </p>
        <p className="nr-reason">{item.reason_text}</p>
        {error ? (
          <p className="nr-error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
      {item.can_send_now ? (
        <Button
          type="button"
          kind="secondary"
          size="sm"
          disabled={busy}
          aria-describedby={`nr-name-${item.join_id}`}
          onClick={() => control.send(item.join_id)}
        >
          {busy ? "Sending…" : "Send now"}
        </Button>
      ) : null}
    </li>
  );
}
