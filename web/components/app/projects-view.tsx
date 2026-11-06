"use client";

import { useEffect, useRef, useState } from "react";
import { Alert, Button, Icon, PageHead, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { Project, ProjectChanges, ProjectList, Team } from "@/lib/api/types";
import { PROJECT_COLOURS } from "@/lib/brand";
import { lengthLabel, SPRINT_LENGTHS } from "@/lib/sprints";
import { longDay, swatchClass } from "@/lib/tasks";

const COLOURS = PROJECT_COLOURS;
const SAVE_AFTER_MS = 600;
// The Sprint length list of a project: the designer's, Workspace default first, without Custom.
const PROJECT_LENGTHS = SPRINT_LENGTHS.filter(([value]) => value !== "custom");

// The Projects screen (the designer's `isProjects`): the list on the left, and on the right the
// stats, Name, Colour, Recurring meetings, Keywords, Sprint length, Milestone and Delete project.
// "Changes save automatically": the name is saved as it is typed (a short pause, and when the field
// is left), a colour and a keyword at once, each as a PATCH of that one field. Only an owner or
// admin of the team may change anything (`can_manage`); a member sees the same screen with the
// fields switched off and no New project or Delete.
//
// W9: the two sprint figures ("In current sprint", "In backlog") and the Sprint length select are
// live; "Workspace default (...)" names the team's length (`team`, left plain when it could not be
// read). Setting a length gives the project its own sprints and moves its tasks into them.
// Switched off with one "Not available yet" line, because its feature is not built (the same
// pattern as the Settings sections): Recurring meetings (W8). Not in the designer's file, added here:
// the "Only an owner or admin..." line for a member, and the empty list text.
export function ProjectsView({ initial, team = null }: { initial: ProjectList | null; team?: Team | null }) {
  const [data, setData] = useState<ProjectList | null>(initial);
  const [failed, setFailed] = useState(initial === null);
  const [selected, setSelected] = useState<number | null>(initial?.projects[0]?.id ?? null);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [keyword, setKeyword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, flash] = useToast();
  const timers = useRef<Record<number, ReturnType<typeof setTimeout>>>({});
  useEffect(() => {
    const open = timers.current;
    return () => Object.values(open).forEach(clearTimeout);
  }, []);

  const projects = data?.projects ?? [];
  const can = data?.can_manage ?? false;
  const current = projects.find((p) => p.id === selected) ?? projects[0] ?? null;

  // The answer replaces the project; a name typed since is still shown (`drafts` wins on screen).
  function merge(next: Project) {
    setData((d) => (d ? { ...d, projects: d.projects.map((p) => (p.id === next.id ? next : p)) } : d));
  }

  async function reload() {
    setFailed(false);
    try {
      const next = await api.projects.list();
      setData(next);
      setSelected((s) => (next.projects.some((p) => p.id === s) ? s : (next.projects[0]?.id ?? null)));
    } catch {
      setFailed(true);
    }
  }

  async function save(project: Project, changes: ProjectChanges) {
    setError(null);
    try {
      merge(await api.projects.update(project.id, changes));
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  function typeName(project: Project, value: string) {
    setDrafts((d) => ({ ...d, [project.id]: value }));
    clearTimeout(timers.current[project.id]);
    timers.current[project.id] = setTimeout(() => void commitName(project, value), SAVE_AFTER_MS);
  }

  async function commitName(project: Project, value: string) {
    clearTimeout(timers.current[project.id]);
    delete timers.current[project.id];
    if (value === project.name) return;
    // What was typed stays on screen while it saves; a later edit is not overwritten by the answer.
    await save(project, { name: value });
    setDrafts((d) => {
      const rest = { ...d };
      if (rest[project.id] === value) delete rest[project.id];
      return rest;
    });
  }

  async function addProject() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const made = await api.projects.create({});
      setData((d) => (d ? { ...d, projects: [...d.projects, made] } : d));
      setSelected(made.id);
      setKeyword("");
    } catch (caught) {
      flash(errorMessage(caught));
    }
    setBusy(false);
  }

  async function removeProject(project: Project) {
    if (busy) return;
    setBusy(true);
    try {
      await api.projects.remove(project.id);
      const rest = projects.filter((p) => p.id !== project.id);
      setData((d) => (d ? { ...d, projects: rest } : d));
      setSelected(rest[0]?.id ?? null);
      flash("Project deleted. Its tasks now have no project.");
    } catch (caught) {
      flash(errorMessage(caught));
    }
    setBusy(false);
  }

  function addKeyword(project: Project) {
    const word = keyword.trim();
    setKeyword("");
    if (!word || project.keywords.includes(word)) return;
    void save(project, { keywords: [...project.keywords, word] });
  }

  if (failed || data === null) {
    return (
      <div className="stack-lg">
        <PageHead title="Projects" subtitle="Tasks are filed into a project using its recurring meetings and keywords." />
        <Alert kind="error">
          <span>Could not load projects. </span>
          <button type="button" className="btn btn-link" onClick={() => void reload()}>
            Try again
          </button>
        </Alert>
      </div>
    );
  }

  return (
    <div className="stack-lg pj">
      <PageHead title="Projects" subtitle="Tasks are filed into a project using its recurring meetings and keywords.">
        {can ? (
          <Button type="button" size="md" onClick={() => void addProject()} disabled={busy}>
            <Icon name="plus" size={14} stroke={2.4} />
            New project
          </Button>
        ) : null}
      </PageHead>
      <div className="pj-layout">
        <nav className="pj-list" aria-label="Projects">
          {projects.length === 0 ? <p className="pj-none">No projects yet.</p> : null}
          {projects.map((p) => (
            <button
              key={p.id}
              type="button"
              className={`pj-item${current?.id === p.id ? " pj-item-on" : ""}`}
              aria-current={current?.id === p.id}
              onClick={() => {
                setSelected(p.id);
                setKeyword("");
                setError(null);
              }}
            >
              <span className={`pj-swatch ${swatchClass(p.color)}`} />
              <span className="pj-item-text">
                <span className="pj-item-name">{drafts[p.id] ?? (p.name || "Untitled project")}</span>
                <span className="pj-item-sub">{p.open_tasks} open tasks</span>
              </span>
            </button>
          ))}
        </nav>
        {current ? (
          <section className="pj-detail card" aria-label={current.name || "Untitled project"}>
            <div className="pj-stats">
              <div className="pj-stat">
                <div className="pj-stat-value">{current.open_tasks}</div>
                <div className="pj-stat-label">Open tasks</div>
              </div>
              <div className="pj-stat">
                <div className="pj-stat-value">{current.in_current_sprint}</div>
                <div className="pj-stat-label">In current sprint</div>
              </div>
              <div className="pj-stat">
                <div className="pj-stat-value">{current.in_backlog}</div>
                <div className="pj-stat-label">In backlog</div>
              </div>
            </div>
            {!can ? <p className="meta pj-soon">Only an owner or admin of your team can change projects.</p> : null}
            {error ? <Alert kind="error">{error}</Alert> : null}
            <label className="pj-field">
              <span className="pj-label">Name</span>
              <input
                className="input"
                value={drafts[current.id] ?? current.name}
                placeholder="e.g. Client onboarding"
                maxLength={100}
                readOnly={!can}
                onChange={(event) => typeName(current, event.target.value)}
                onBlur={(event) => can && void commitName(current, event.target.value)}
              />
            </label>
            <div className="pj-field">
              <span className="pj-label" id="pj-colour">
                Colour
              </span>
              <div className="pj-swatches" role="group" aria-labelledby="pj-colour">
                {COLOURS.map((colour) => (
                  <button
                    key={colour}
                    type="button"
                    title={colour}
                    aria-label={colour}
                    aria-pressed={current.color.toLowerCase() === colour.toLowerCase()}
                    disabled={!can}
                    className={`pj-colour ${swatchClass(colour)}${current.color.toLowerCase() === colour.toLowerCase() ? " pj-colour-on" : ""}`}
                    onClick={() => void save(current, { color: colour })}
                  />
                ))}
              </div>
            </div>
            <div className="pj-field">
              <div>
                <div className="pj-label">Recurring meetings</div>
                <div className="pj-help">Action items from these meetings go into this project.</div>
              </div>
              <p className="meta pj-off-line">Not available yet.</p>
            </div>
            <div className="pj-field">
              <div>
                <div className="pj-label">Keywords</div>
                <div className="pj-help">Used to file tasks from meetings that are not linked above.</div>
              </div>
              <div className="pj-keywords">
                {current.keywords.map((word) => (
                  <span className="pj-chip" key={word}>
                    {word}
                    {can ? (
                      <button
                        type="button"
                        className="pj-chip-x"
                        aria-label={`Remove ${word}`}
                        onClick={() => void save(current, { keywords: current.keywords.filter((k) => k !== word) })}
                      >
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" aria-hidden="true">
                          <path d="M6 6l12 12M18 6 6 18" />
                        </svg>
                      </button>
                    ) : null}
                  </span>
                ))}
                {can ? (
                  <input
                    className="pj-kw-input"
                    aria-label="Add a keyword"
                    value={keyword}
                    maxLength={40}
                    placeholder="Add a keyword, press Enter"
                    onChange={(event) => setKeyword(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === ",") {
                        event.preventDefault();
                        addKeyword(current);
                      }
                    }}
                  />
                ) : null}
              </div>
            </div>
            <div className="pj-pair">
              <div className="pj-field pj-half">
                <label className="pj-label" htmlFor="pj-sprint">
                  Sprint length
                </label>
                <select
                  id="pj-sprint"
                  className="select"
                  value={current.sprint_length ?? ""}
                  disabled={!can}
                  onChange={(event) => void save(current, { sprint_length: (event.target.value || null) as Project["sprint_length"] })}
                >
                  <option value="">
                    {team ? `Workspace default (${lengthLabel(team.sprint_length, team.sprint_custom_days)})` : "Workspace default"}
                  </option>
                  {PROJECT_LENGTHS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="pj-field pj-half">
                <span className="pj-label">Milestone</span>
                <div className="pj-milestone">
                  {current.milestone ? `${current.milestone.name}, ${longDay(current.milestone.due_date)}` : "None yet"}
                </div>
              </div>
            </div>
            <div className="pj-foot">
              {can ? (
                <button type="button" className="pj-delete" disabled={busy} onClick={() => void removeProject(current)}>
                  Delete project
                </button>
              ) : (
                <span />
              )}
              {can ? <span className="pj-saves">Changes save automatically</span> : null}
            </div>
          </section>
        ) : null}
      </div>
      <Toast message={toast} />
    </div>
  );
}
