"use client";

import type { Sprint } from "@/lib/api/types";

// The sprint card above the Tasks board (the designer's `sp` block): the sprint's name and dates,
// the pill with how long it has left, a progress bar, "x of y done" with the line about how sprints
// open and close, and Close sprint on the current one. `canClose` is false for a member (the API
// refuses them) and for a sprint that is not the current one.
export function SprintHeader({
  sprint,
  range,
  timing,
  note,
  done,
  total,
  canClose,
  closing,
  onClose,
}: {
  sprint: Sprint;
  range: string;
  timing: string;
  note: string;
  done: number;
  total: number;
  canClose: boolean;
  closing: boolean;
  onClose: () => void;
}) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  const progress = `${done} of ${total} done`;
  return (
    <div className="tk-sprint">
      <div className="tk-sprint-top">
        <div className="tk-sprint-name">
          <span className="tk-sprint-title">{sprint.name}</span>
          <span className="tk-sprint-range">{range}</span>
        </div>
        <span className={`tk-sprint-pill${sprint.state === "current" ? " tk-sprint-pill-on" : ""}`}>{timing}</span>
      </div>
      <progress className="tk-ms-bar tk-sprint-bar" max={100} value={pct} aria-label={progress} />
      <div className="tk-sprint-foot">
        <span className="tk-sprint-progress">
          {progress} · {note}
        </span>
        {canClose ? (
          <button type="button" className="tk-sprint-close" disabled={closing} onClick={onClose}>
            Close sprint
          </button>
        ) : null}
      </div>
    </div>
  );
}
