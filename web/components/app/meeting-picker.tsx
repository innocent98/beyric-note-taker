"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Checkbox, Field, SelectField } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { MeetingSummary, PickerPreview, PickerSpec, Project, SeriesCard, Sprint } from "@/lib/api/types";
import { rangeProblem } from "@/lib/date-range";
import { browserTimeZone, MONTHS } from "@/lib/format";

// LEAD-DESIGNED, not in the designer's files (recipes spec 5.3b, plan T15): the meeting picker of the
// cross-meeting recipes (Status report, Client briefing, Revision plan), the body of the Make dialog's
// "Meetings" step. Built from the app's own select, field and checkbox styles. A person chooses ONE
// way (a recurring meeting, a project, a sprint, a date range, or meetings picked by hand) and sees
// at once which meetings that means: at most 12, newest first. Meetings they cannot read are left
// out and counted ("2 meetings were left out ..."), never named. The preview comes from the server
// (`POST /api/recipes/picker/resolve`) so what is shown is exactly what the draft will read.
const WAYS = [
  ["series", "A recurring meeting"],
  ["project", "A project"],
  ["sprint", "A sprint"],
  ["dates", "A date range"],
  ["pick", "Pick meetings"],
] as const;
type Way = (typeof WAYS)[number][0];

export const MAX_MEETINGS = 12;
const RECENT = 50;
const GONE = "That choice is no longer available. Choose again.";

export type MeetingSelection = {
  // What to send as `picker` with the draft request (null until a choice is complete).
  picker: PickerSpec | null;
  // The preview's meeting ids, newest first: send them as `meeting_ids`.
  meetingIds: number[];
  // Chosen meetings the person cannot read: the draft keeps the count.
  leftOut: number;
};

const NOTHING: MeetingSelection = { picker: null, meetingIds: [], leftOut: 0 };

function dayLabel(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

export function MeetingPicker({
  id,
  team,
  onChange,
}: {
  id: string;
  // The active team (`X-Team-Id`): undefined or null is the person's default.
  team?: number | null;
  onChange: (selection: MeetingSelection) => void;
}) {
  const [way, setWay] = useState<Way>("series");
  const [seriesId, setSeriesId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [sprintId, setSprintId] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [picked, setPicked] = useState<number[]>([]);
  const [series, setSeries] = useState<SeriesCard[] | null>(null);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [sprints, setSprints] = useState<Sprint[] | null>(null);
  const [recent, setRecent] = useState<MeetingSummary[] | null>(null);
  // The server's answer to ONE choice: kept with the choice it answers, so a changed choice shows
  // "Looking for meetings..." until its own answer comes and an older answer is never shown.
  const [result, setResult] = useState<{ key: string; preview: PickerPreview | null; error: string | null } | null>(null);
  const [listError, setListError] = useState<{ way: Way; message: string } | null>(null);
  const report = useRef(onChange);
  useEffect(() => {
    report.current = onChange;
  });

  // What the person has chosen, once it is complete enough to ask the server about.
  const rangeError = way === "dates" ? rangeProblem(from || null, to || null) : null;
  const spec = useMemo<PickerSpec | null>(() => {
    if (way === "series") return seriesId ? { series_id: Number(seriesId) } : null;
    if (way === "project") return projectId ? { project_id: Number(projectId) } : null;
    if (way === "sprint") return sprintId ? { sprint_id: Number(sprintId) } : null;
    if (way === "pick") return picked.length ? { meeting_ids: picked } : null;
    if (!(from || to) || rangeError) return null;
    return { ...(from ? { from_day: from } : {}), ...(to ? { to_day: to } : {}), tz: browserTimeZone() };
  }, [way, seriesId, projectId, sprintId, picked, from, to, rangeError]);
  const specKey = JSON.stringify(spec);

  // The lists a way needs are read when that way is first chosen, so the dialog costs nothing until used.
  useEffect(() => {
    let live = true;
    const failed = (caught: unknown) => {
      if (live) setListError({ way, message: errorMessage(caught) });
    };
    if (way === "series" && series === null) api.series.list(team).then((a) => live && setSeries(a.series), failed);
    if (way === "project" && projects === null) api.projects.list(team).then((a) => live && setProjects(a.projects), failed);
    if (way === "sprint" && sprints === null) api.sprints.list(undefined, team).then((a) => live && setSprints(a.sprints), failed);
    if (way === "pick" && recent === null) api.meetings.list({ limit: RECENT }).then((a) => live && setRecent(a.meetings), failed);
    return () => {
      live = false;
    };
  }, [way, team, series, projects, sprints, recent]);

  // A different team has different recurring meetings, projects and sprints: forget what was chosen.
  const [seenTeam, setSeenTeam] = useState(team);
  if (seenTeam !== team) {
    setSeenTeam(team);
    setSeries(null);
    setProjects(null);
    setSprints(null);
    setSeriesId("");
    setProjectId("");
    setSprintId("");
  }

  // The preview follows the choice. A reply to an older choice is dropped.
  const resultKey = `${team ?? ""}:${specKey}`;
  useEffect(() => {
    if (specKey === "null") {
      report.current(NOTHING);
      return;
    }
    const chosen: PickerSpec = JSON.parse(specKey);
    let live = true;
    api.recipes.resolvePicker(chosen, team).then(
      (answer) => {
        if (!live) return;
        setResult({ key: resultKey, preview: answer, error: null });
        report.current({ picker: chosen, meetingIds: answer.meetings.map((m) => m.id), leftOut: answer.left_out_count });
      },
      (caught) => {
        if (!live) return;
        // A 404 is a choice that went away since the list was read (a removed project, a team left).
        const message = caught instanceof ApiError && caught.status === 404 ? GONE : errorMessage(caught);
        setResult({ key: resultKey, preview: null, error: message });
        report.current(NOTHING);
      },
    );
    return () => {
      live = false;
    };
  }, [specKey, team, resultKey]);
  const answer = result && result.key === resultKey ? result : null;
  const busy = specKey !== "null" && answer === null;
  const preview = answer?.preview ?? null;
  const error = answer?.error ?? (listError && listError.way === way ? listError.message : null);

  function toggle(meetingId: number) {
    setPicked((now) => (now.includes(meetingId) ? now.filter((n) => n !== meetingId) : [...now, meetingId]));
  }

  return (
    <div className="mp">
      <SelectField
        id={`${id}-way`}
        label="Choose meetings by"
        value={way}
        onChange={(event) => setWay(event.target.value as Way)}
        options={WAYS}
      />
      {way === "series" ? (
        <SelectField
          id={`${id}-series`}
          label="Recurring meeting"
          value={seriesId}
          onChange={(event) => setSeriesId(event.target.value)}
          options={[["", "Choose a recurring meeting"], ...(series ?? []).map((s) => [String(s.id), s.title] as const)]}
        />
      ) : null}
      {way === "project" ? (
        <SelectField
          id={`${id}-project`}
          label="Project"
          value={projectId}
          onChange={(event) => setProjectId(event.target.value)}
          options={[["", "Choose a project"], ...(projects ?? []).map((p) => [String(p.id), p.name] as const)]}
        />
      ) : null}
      {way === "sprint" ? (
        <SelectField
          id={`${id}-sprint`}
          label="Sprint"
          value={sprintId}
          onChange={(event) => setSprintId(event.target.value)}
          options={[
            ["", "Choose a sprint"],
            ...(sprints ?? []).map((s) => [String(s.id), s.state === "closed" ? s.name : `${s.name} (${s.state})`] as const),
          ]}
        />
      ) : null}
      {way === "dates" ? (
        <div className="mp-dates">
          <Field id={`${id}-from`} label="From" type="date" value={from} onChange={(event) => setFrom(event.target.value)} />
          <Field id={`${id}-to`} label="To" type="date" value={to} onChange={(event) => setTo(event.target.value)} />
        </div>
      ) : null}
      {rangeError ? <Alert kind="error">{rangeError}</Alert> : null}
      {way === "pick" ? (
        <fieldset className="mp-pick">
          <legend className="field-label">Meetings (up to {MAX_MEETINGS})</legend>
          {recent === null ? <p className="mp-note">Loading your meetings...</p> : null}
          {recent?.map((meeting) => (
            <Checkbox
              key={meeting.id}
              id={`${id}-m${meeting.id}`}
              checked={picked.includes(meeting.id)}
              disabled={!picked.includes(meeting.id) && picked.length >= MAX_MEETINGS}
              onChange={() => toggle(meeting.id)}
            >
              {meeting.title}
            </Checkbox>
          ))}
        </fieldset>
      ) : null}

      <div role="status" aria-live="polite" className="mp-result">
        {error ? <Alert kind="error">{error}</Alert> : null}
        {busy ? <p className="mp-note">Looking for meetings...</p> : null}
        {preview && !busy ? (
          <>
            {preview.meetings.length ? (
              <>
                <p className="mp-count">
                  {preview.meetings.length === 1 ? "1 meeting" : `${preview.meetings.length} meetings`} will be used
                </p>
                <ul className="mp-list">
                  {preview.meetings.map((meeting) => (
                    <li key={meeting.id} className="mp-item">
                      <span className="mp-title">{meeting.title}</span>
                      <span className="mp-day">{dayLabel(meeting.day)}</span>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="mp-note">No meetings with notes match that choice.</p>
            )}
            {preview.capped ? (
              <Alert kind="info">
                More than {preview.max_meetings} meetings match. The {preview.max_meetings} newest are used.
              </Alert>
            ) : null}
            {preview.left_out_message ? <Alert kind="notice">{preview.left_out_message}</Alert> : null}
          </>
        ) : null}
      </div>
    </div>
  );
}
