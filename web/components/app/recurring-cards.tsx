"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/browser";
import type { RecurringSeries } from "@/lib/api/types";

// The Recurring cards of the Meetings screen (the designer's, in `Beyric Note Taker.dc.html`),
// built from the calendar's own recurring events: GET /api/calendar/recurring. The server writes
// the cadence and the next date in the viewer's zone, so the request carries the browser's zone and
// the page asks after it has loaded (the server cannot know it).
//
// Drawn: the name, the cadence and the "Next:" chip. Not drawn, because nothing real backs them yet:
// the unfinished-tasks and open-questions chips (tasks and series carry-over are later work) and the
// chevron: the card is not a link, the Series page does not exist. Nothing at all is drawn while
// waiting, when no calendar is connected, when nothing recurs, and when the call fails for any
// reason: the Meetings page must look as it did before.
export function RecurringCards() {
  const [series, setSeries] = useState<RecurringSeries[]>([]);

  useEffect(() => {
    let current = true;
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    api.calendar
      .recurring(zone)
      .then((answer) => {
        if (current) setSeries(answer.series);
      })
      .catch(() => {
        // Calendar off, zone refused, server or network trouble: draw nothing.
      });
    return () => {
      current = false;
    };
  }, []);

  if (series.length === 0) return null;
  return (
    <section className="rec" aria-labelledby="recurring-title">
      <h2 id="recurring-title" className="rec-title">
        Recurring
      </h2>
      <ul className="rec-grid">
        {series.map((card, index) => (
          <li key={`${index}-${card.name}`} className="rec-card">
            <div>
              <div className="rec-name">{card.name}</div>
              <div className="rec-cadence">{card.cadence}</div>
            </div>
            <div className="rec-chips">
              <span className="rec-chip">Next: {card.next_text}</span>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
