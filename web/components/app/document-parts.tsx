"use client";

import { Fragment } from "react";
import { AppLink } from "@/components/ui";
import type { DocumentSource } from "@/lib/api/types";
import { highlightRuns, sourceHint, sourceHref, sourceLabel } from "@/lib/documents";

// The text of a draft with its blanks (`[need: ...]`) and, when `flagged` names them, the figures the
// server could not find in the meeting marked "check this".
export function Marked({ text, flagged = [] }: { text: string; flagged?: string[] }) {
  return (
    <>
      {highlightRuns(text, flagged).map((run, i) =>
        run.kind === "blank" ? (
          <mark key={i} className="doc-blank">
            {run.text}
          </mark>
        ) : run.kind === "check" ? (
          <mark key={i} className="doc-check" title="check this">
            {run.text}
            <span className="visually-hidden"> (check this)</span>
          </mark>
        ) : (
          <Fragment key={i}>{run.text}</Fragment>
        ),
      )}
    </>
  );
}

// "Drafted from" and one chip per source: a line opens the transcript at that row, anything else opens
// the meeting. A source the reader may not open (`hidden`) and, when none of the draft's meetings can be
// opened, every source read "not available" and link nowhere (one such chip, however many sources). An
// edited block keeps its sources, so the words stay "drafted from".
export function SourceChips({ sources, available }: { sources: DocumentSource[]; available: boolean }) {
  if (sources.length === 0) return null;
  const links = available
    ? sources.flatMap((source) => {
        const href = sourceHref(source);
        return href === null ? [] : [{ source, href }];
      })
    : [];
  const closed = !available || links.length < sources.length;
  return (
    <p className="doc-sources">
      <span>Drafted from</span>
      {links.map(({ source, href }, i) => (
        <AppLink key={i} className="doc-chip" href={href} title={sourceHint(source)}>
          {sourceLabel(source)}
        </AppLink>
      ))}
      {closed ? <span className="doc-chip doc-chip-off">not available</span> : null}
    </p>
  );
}
