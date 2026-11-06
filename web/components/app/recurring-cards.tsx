"use client";

import { useEffect, useState } from "react";
import { AppLink, Icon } from "@/components/ui";
import { api } from "@/lib/api/browser";
import type { SeriesCard } from "@/lib/api/types";
import { plural } from "@/lib/format";

// The Recurring cards of the Meetings screen (the designer's, in `Beyric Note Taker.dc.html`):
// the team's recurring meetings from GET /api/series (docs/fe-integration-guide-series.md). A card
// is a link to the Series page and carries three chips: "Next: <date>", "N unfinished tasks" and
// "N open questions". The server writes the cadence and the date in the viewer's saved zone, and
// counts only what the viewer may read.
//
// Nothing at all is drawn while waiting, when the team has no recurring meeting, and when the call
// fails for any reason: the Meetings page must look as it did before. A recurring calendar event
// appears here once its join is scheduled (within the hour) or its first notes are written.
export function RecurringCards() {
  const [series, setSeries] = useState<SeriesCard[]>([]);

  useEffect(() => {
    let current = true;
    api.series
      .list()
      .then((answer) => {
        if (current) setSeries(answer.series);
      })
      .catch(() => {
        // Server or network trouble: draw nothing.
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
        {series.map((card) => (
          <li key={card.id} className="rec-item">
            <AppLink href={`/series/${card.id}`} className="rec-card">
              <div className="rec-top">
                <div>
                  <div className="rec-name">{card.title}</div>
                  <div className="rec-cadence">{card.cadence}</div>
                </div>
                <span className="rec-chev">
                  <Icon name="chevron" size={16} />
                </span>
              </div>
              <div className="rec-chips">
                {card.next_text ? <span className="rec-chip">Next: {card.next_text}</span> : null}
                <span className="rec-chip rec-chip-plain">{plural(card.unfinished_tasks, "unfinished task")}</span>
                <span className="rec-chip rec-chip-plain">{plural(card.open_questions, "open question")}</span>
              </div>
            </AppLink>
          </li>
        ))}
      </ul>
    </section>
  );
}
