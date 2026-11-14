"use client";

import { useState, type FormEvent } from "react";
import { Alert, Button, Card, Field } from "@/components/ui";
import { isFiltered, presets, rangeProblem, type DayRange } from "@/lib/date-range";

// The date filter above the Meetings list (lead-designed: the designer's files have no filter, so it
// is built from the existing Card, Field and Button styles). Two date boxes and an Apply button (a
// plain form: Enter applies; `noValidate` so the words below, not the browser's own bubble, explain a
// From that is after To, the same words everywhere), three quick ranges that apply at once, and Clear. Typing a date is
// never applied half way: a date box says "" until its date is whole, so applying happens on Apply,
// Enter or a quick button, not on every keystroke. `today` is today's day in the owner's zone, the
// zone the server's days are in as well.
export function MeetingFilter({ range, today, onApply }: { range: DayRange; today: string | null; onApply: (range: DayRange) => void }) {
  const [from, setFrom] = useState(range.from ?? "");
  const [to, setTo] = useState(range.to ?? "");
  const [error, setError] = useState<string | null>(null);
  // The address is the truth: when it changes under us (the Back button, a quick button) the boxes follow.
  const [shown, setShown] = useState(range);
  if (shown.from !== range.from || shown.to !== range.to) {
    setShown(range);
    setFrom(range.from ?? "");
    setTo(range.to ?? "");
    setError(null);
  }

  function apply(next: DayRange) {
    const problem = rangeProblem(next.from, next.to);
    setError(problem);
    if (problem === null) onApply(next);
  }

  function quick(next: DayRange) {
    setFrom(next.from ?? "");
    setTo(next.to ?? "");
    apply(next);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    apply({ from: from || null, to: to || null });
  }

  const ranges = today ? presets(today) : null;
  return (
    <Card compact>
      <form className="stack-sm" onSubmit={submit} aria-label="Filter meetings by date" noValidate>
        <div className="row-wrap">
          <div className="field-fixed">
            <Field id="meetings-from" label="From" type="date" value={from} max={to || undefined} onChange={(event) => setFrom(event.target.value)} />
          </div>
          <div className="field-fixed">
            <Field id="meetings-to" label="To" type="date" value={to} min={from || undefined} onChange={(event) => setTo(event.target.value)} />
          </div>
          <Button kind="secondary" size="sm">
            Apply
          </Button>
        </div>
        <div className="row-wrap">
          <Button kind="secondary" size="sm" type="button" disabled={!ranges} onClick={() => ranges && quick(ranges.today)}>
            Today
          </Button>
          <Button kind="secondary" size="sm" type="button" disabled={!ranges} onClick={() => ranges && quick(ranges.last7)}>
            Last 7 days
          </Button>
          <Button kind="secondary" size="sm" type="button" disabled={!ranges} onClick={() => ranges && quick(ranges.last30)}>
            Last 30 days
          </Button>
          {isFiltered(range) || from || to ? (
            <Button kind="ghost" size="sm" type="button" onClick={() => quick({ from: null, to: null })}>
              Clear
            </Button>
          ) : null}
        </div>
        {error ? (
          <Alert kind="error" role="alert">
            {error}
          </Alert>
        ) : null}
      </form>
    </Card>
  );
}
