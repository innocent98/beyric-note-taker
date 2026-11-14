"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Alert, Badge, Button, Card, EmptyState, Icon, ListRow, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { DELETED_FLAG } from "@/lib/deleted-flag";
import { api } from "@/lib/api/browser";
import type { MeetingList as MeetingListData, MeetingSummary, Template } from "@/lib/api/types";
import { LocalTime, useDisplayTimeZone } from "@/components/app/local-time";
import { MeetingFilter } from "@/components/app/meeting-filter";
import { NO_RANGE, isFiltered, rangeHeading, rangeQuery, type DayRange } from "@/lib/date-range";
import { dayKey, groupByDay } from "@/lib/day-groups";
import { plural } from "@/lib/format";

// The list of meetings, newest first. The first page comes from the server render; older pages are
// fetched on request with before_id (docs/fe-integration-guide-owner-app.md 5.1). The page asks for a
// fresh first page every `refresh_after_seconds` while a listed meeting is being worked on (the
// status words change), and when the tab comes back into view.
//
// A heading above each day (Today, Yesterday, "Mon 9 Nov"; `lib/day-groups.ts`) in the zone the rows'
// own times are written in. A meeting is under the day of the `created_at` its row shows. The groups
// are made from the whole list (first page and every older page together), so a day that runs over
// a page boundary stays under one heading.
//
// The date filter (`range`, lead-designed; components/app/meeting-filter.tsx) is in the address
// (`/meetings?from=&to=`): applying it navigates, the server renders the first page of that range, and
// "Show older meetings" asks for the same range. The refresh above keeps the address, so it keeps the
// filter. `rangeNotice` says why a damaged address was ignored.
export function MeetingList({
  initial,
  templates,
  range = NO_RANGE,
  rangeNotice = null,
}: {
  initial: MeetingListData;
  templates: Template[];
  range?: DayRange;
  rangeNotice?: string | null;
}) {
  const router = useRouter();
  // Older pages belong to the range they were fetched for: another range starts again from the
  // server's first page, with no effect to reset anything.
  const rangeId = rangeQuery(range);
  const [paged, setPaged] = useState<{ rangeId: string; older: MeetingSummary[]; hasMore: boolean; before: number | null } | null>(null);
  const current = paged && paged.rangeId === rangeId ? paged : null;
  const older = current?.older ?? [];
  const paging = current ? { hasMore: current.hasMore, before: current.before } : null;
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [toast, flash] = useToast();
  const timeZone = useDisplayTimeZone();
  const now = useNow();

  // Arriving from a delete on the meeting page: say so once.
  useEffect(() => {
    try {
      if (sessionStorage.getItem(DELETED_FLAG)) {
        sessionStorage.removeItem(DELETED_FLAG);
        flash("Meeting deleted");
      }
    } catch {
      // No storage: no toast.
    }
  }, [flash]);

  // Refreshing re-renders the server component with a new `initial`, so the interval stops by
  // itself once nothing listed is being worked on (it becomes null).
  const interval = initial.refresh_after_seconds;
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "visible") router.refresh();
    };
    const timer = interval === null ? undefined : setInterval(refresh, interval * 1000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      if (timer !== undefined) clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [router, interval]);

  const shown = new Set(initial.meetings.map((m) => m.id));
  const meetings = [...initial.meetings, ...older.filter((m) => !shown.has(m.id))];
  const hasMore = paging ? paging.hasMore : initial.has_more;
  const before = paging ? paging.before : initial.next_before_id;
  const names = new Map(templates.map((t) => [t.id, t.name]));
  const days = groupByDay(meetings, timeZone, now);
  const filtered = isFiltered(range);
  const today = dayKey(now.toISOString(), timeZone);

  async function loadOlder() {
    if (before === null || loading) return;
    setLoading(true);
    setFailed(false);
    try {
      const page = await api.meetings.list({ before_id: before, from: range.from ?? undefined, to: range.to ?? undefined });
      setPaged({
        rangeId,
        older: [...older, ...page.meetings],
        hasMore: page.has_more,
        before: page.next_before_id,
      });
    } catch {
      setFailed(true);
    }
    setLoading(false);
  }

  function applyRange(next: DayRange) {
    router.push(`/meetings${rangeQuery(next)}`, { scroll: false });
  }

  const head = (
    <div className="card-head">
      <h2 suppressHydrationWarning>{rangeHeading(range, today)}</h2>
      <span className="meta">
        {plural(meetings.length, "meeting")}
        {hasMore ? "+" : ""}
      </span>
    </div>
  );

  return (
    <>
    {meetings.length > 0 || filtered ? <MeetingFilter range={range} today={today} onApply={applyRange} /> : null}
    {rangeNotice ? <Alert kind="notice">{rangeNotice}</Alert> : null}
    <Card flush>
      {meetings.length > 0 ? (
        <>
          {head}
          {days.map((day) => (
            <div className="day-group" key={day.key}>
              <h3 className="day-head" id={`meetings-day-${day.key}`} suppressHydrationWarning>
                {day.label}
              </h3>
              <ul className="list" aria-labelledby={`meetings-day-${day.key}`}>
                {day.items.map((m) => (
                  <ListRow
                    key={m.id}
                    href={`/meetings/${m.id}`}
                    end={
                      <span className="row-end">
                        <Badge tone={m.status_tone} dot>
                          {m.status_text}
                        </Badge>
                        <span className="list-row-chevron">
                          <Icon name="chevron" />
                        </span>
                      </span>
                    }
                  >
                    <span className="list-row-main">
                      <span className="tile">
                        <Icon name="video" size={18} />
                      </span>
                      <span className="list-row-text">
                        <span className="list-title">{m.title}</span>
                        <span className="meta-line">
                          <span>
                            <LocalTime iso={m.created_at} />
                          </span>
                          {names.get(m.template_id) ?? m.template_name ? (
                            <span>{names.get(m.template_id) ?? m.template_name}</span>
                          ) : null}
                          {m.tasks_count > 0 ? <span className="meta-tasks">{plural(m.tasks_count, "task")}</span> : null}
                        </span>
                      </span>
                    </span>
                  </ListRow>
                ))}
              </ul>
            </div>
          ))}
          {hasMore ? (
            <div className="card-foot">
              <Button kind="secondary" size="sm" type="button" onClick={loadOlder} disabled={loading}>
                {loading ? "Loading..." : "Show older meetings"}
              </Button>
              {failed ? <span className="meta">Could not load older meetings. Try again.</span> : null}
            </div>
          ) : null}
        </>
      ) : filtered ? (
        <>
          {head}
          <EmptyState title="No meetings in this range.">Try other dates, or clear the filter to see every meeting.</EmptyState>
        </>
      ) : (
        <EmptyState title="No meetings yet.">Paste a meeting link above to send the note taker.</EmptyState>
      )}
    </Card>
    <Toast message={toast} />
    </>
  );
}

// The current time, renewed every minute and whenever the tab comes back into view, so "Today" and
// "Yesterday" move on at midnight in a page left open. The first value is the render's own clock.
function useNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const tick = () => setNow(new Date());
    const timer = setInterval(tick, 60_000);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, []);
  return now;
}
