"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { GateText } from "@/components/billing/gate-text";
import { Alert, AppLink, Button, Field, Icon, SelectField } from "@/components/ui";
import { api } from "@/lib/api/browser";
import type { Brief, Recipe, RecipeList, TeamSummary } from "@/lib/api/types";
import { defaultOptions, groupRecipes, optionsToSend, recipeErrorMessage, recipeRef } from "@/lib/recipes";

// What a place for the meeting picker is handed: the meetings chosen so far (this one to begin with) and
// the way to change them. Slice S5 mounts its picker through `renderMeetingPicker`.
export type MeetingPickerProps = { meetingIds: number[]; onChange: (ids: number[]) => void };

const OUTPUT_LABEL: Record<Recipe["output_type"], string> = { document: "Document", email: "Email", table: "Table", board: "Board" };

function valueOf(recipe: Recipe): string {
  return recipe.id === null ? (recipe.key ?? "") : String(recipe.id);
}

// "Make from this meeting": the lead-designed dialog behind the Make button on a ready meeting (the
// designer's files draw nothing of recipes). Pick a recipe (General and School starters, the team's own,
// the person's own), fill its choices, see whose brief the draft will use and switch the team, then Make
// draft: the draft is asked for (202) and the draft page opens, which polls while it is written.
// A recipe that needs several meetings (`scope` `many`) gets a marked place for the meeting picker; with
// none given it explains that this meeting alone is used. A recipe for one meeting always uses the page's.
export function MakeDialog({
  open,
  meetingId,
  title,
  onClose,
  renderMeetingPicker,
}: {
  open: boolean;
  meetingId: number;
  title: string;
  onClose: () => void;
  renderMeetingPicker?: (props: MeetingPickerProps) => ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const router = useRouter();
  const [list, setList] = useState<RecipeList | null>(null);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [teams, setTeams] = useState<TeamSummary[]>([]);
  const [teamId, setTeamId] = useState<number | null>(null);
  const [reload, setReload] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  // What was typed or chosen, per recipe: a recipe not touched yet shows its defaults, and a recipe that is
  // switched to starts clean on this meeting alone.
  const [entered, setEntered] = useState<Record<string, Record<string, string>>>({});
  const [useBrief, setUseBrief] = useState(true);
  const [chosen, setChosen] = useState<{ recipe: string | null; ids: number[] }>({ recipe: null, ids: [] });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const asking = useRef(false);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setTeamId(null);
      setList(null);
      setBrief(null);
      setPicked(null);
      setUseBrief(true);
      setEntered({});
      setChosen({ recipe: null, ids: [] });
      setBusy(false);
      setError(null);
      asking.current = false;
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    if (!open && dialog.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
    // Opening resets the form from the props of that moment.
     
  }, [open]);

  // The person's teams, once per opening: with more than one the dialog offers the switch. A failure
  // (the switch is off, or the call failed) is simply no switch.
  useEffect(() => {
    if (!open) return;
    let live = true;
    api.teams
      .list()
      .then((found) => live && setTeams(found.teams))
      .catch(() => live && setTeams([]));
    return () => {
      live = false;
    };
  }, [open]);

  // The recipes and the brief of the team in use (the tab's, until the person switches).
  useEffect(() => {
    if (!open) return;
    let live = true;
    Promise.all([api.recipes.list(teamId ?? undefined), api.brief.get(teamId ?? undefined)])
      .then(([recipes, found]) => {
        if (!live) return;
        setList(recipes);
        setBrief(found);
        setPicked((current) => (current && recipes.recipes.some((r) => valueOf(r) === current) ? current : valueOf(recipes.recipes[0])));
      })
      .catch((caught) => live && setLoadError(recipeErrorMessage(caught)));
    return () => {
      live = false;
    };
  }, [open, teamId, reload]);

  const recipe = list?.recipes.find((r) => valueOf(r) === picked) ?? null;
  const values = (picked !== null ? entered[picked] : undefined) ?? (recipe ? defaultOptions(recipe) : {});
  const setValues = (change: (v: Record<string, string>) => Record<string, string>) => {
    if (picked !== null) setEntered((all) => ({ ...all, [picked]: change(values) }));
  };
  const meetingIds = chosen.recipe === picked ? chosen.ids : [meetingId];
  const setMeetingIds = (ids: number[]) => setChosen({ recipe: picked, ids });

  const many = recipe?.scope === "many";
  const groups = list ? groupRecipes(list.recipes) : null;
  const effectiveTeam = brief?.team ?? list?.team ?? null;

  async function submit() {
    if (!recipe || asking.current) return;
    asking.current = true;
    setBusy(true);
    setError(null);
    try {
      const draft = await api.documents.make(
        {
          ...recipeRef(recipe),
          meeting_ids: many ? meetingIds : [meetingId],
          options: optionsToSend(values),
          use_brief: useBrief && recipe.uses_brief,
        },
        teamId ?? undefined,
      );
      router.push(`/documents/${draft.id}`);
    } catch (caught) {
      setError(recipeErrorMessage(caught));
      asking.current = false;
      setBusy(false);
    }
  }

  const group = (name: string, items: Recipe[]) =>
    items.length === 0 ? null : (
      <fieldset className="mk-group" key={name}>
        <legend className="field-label">{name}</legend>
        <div className="mk-recipes">
          {items.map((r) => (
            <label className="mk-recipe" key={valueOf(r)}>
              <input
                type="radio"
                name="mk-recipe"
                value={valueOf(r)}
                checked={picked === valueOf(r)}
                onChange={() => setPicked(valueOf(r))}
              />
              <span className="mk-recipe-name">{r.name}</span>
              <span className="mk-recipe-meta">
                {OUTPUT_LABEL[r.output_type]}
                {r.scope === "many" ? " · several meetings" : ""}
              </span>
            </label>
          ))}
        </div>
      </fieldset>
    );

  const brokenBrief = brief !== null && brief.text.trim() === "";
  const teamLink = teams.length > 1 && effectiveTeam ? `/recipes?team=${effectiveTeam.id}` : "/recipes";
  return (
    <dialog
      ref={ref}
      className="dialog tm-dialog sh-dialog mk-dialog"
      aria-labelledby="mk-title"
      onClose={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      {open ? (
        <>
          <div className="sh-head">
            <div>
              <h2 className="dialog-title" id="mk-title">
                Make from this meeting
              </h2>
              <p className="tm-dialog-sub">From {title}.</p>
            </div>
            <button type="button" className="sh-close" aria-label="Close" onClick={onClose}>
              <Icon name="close" size={16} />
            </button>
          </div>
          <div className="tm-dialog-body">
            {loadError ? (
              <Alert kind="error">
                {loadError}{" "}
                <Button kind="quiet" type="button" onClick={() => {
                    setLoadError(null);
                    setReload((n) => n + 1);
                  }}>
                  Try again
                </Button>
              </Alert>
            ) : null}
            {teams.length > 1 ? (
              <SelectField
                id="mk-team"
                label="Team"
                value={String(teamId ?? list?.team.id ?? "")}
                options={teams.map((t) => [t.id, t.name] as const)}
                onChange={(event) => {
                  setLoadError(null);
                  setTeamId(Number(event.target.value));
                }}
                hint="Its recipes and its brief are the ones used."
              />
            ) : null}
            {groups ? (
              <div className="mk-pick">
                {group("General", groups.general)}
                {group("School", groups.school)}
                {group("Team recipes", groups.team)}
                {group("My recipes", groups.mine)}
              </div>
            ) : !loadError ? (
              <p className="field-hint" role="status">
                Loading recipes...
              </p>
            ) : null}
            {recipe && Object.keys(recipe.options).length > 0 ? (
              <div className="mk-options">
                {Object.entries(recipe.options).map(([key, option]) =>
                  option.kind === "text" ? (
                    <Field
                      key={`${valueOf(recipe)}-${key}`}
                      id={`mk-opt-${key}`}
                      label={option.label}
                      value={values[key] ?? ""}
                      maxLength={200}
                      onChange={(event) => setValues((v) => ({ ...v, [key]: event.target.value }))}
                    />
                  ) : (
                    <div className="field" key={`${valueOf(recipe)}-${key}`}>
                      <span className="field-label" id={`mk-opt-${key}-label`}>
                        {option.label}
                      </span>
                      <div className="segmented" role="radiogroup" aria-labelledby={`mk-opt-${key}-label`}>
                        {option.choices.map((choice) => (
                          <label className="segmented-opt" key={choice}>
                            <input
                              type="radio"
                              name={`mk-opt-${key}`}
                              value={choice}
                              checked={(values[key] ?? option.default) === choice}
                              onChange={() => setValues((v) => ({ ...v, [key]: choice }))}
                            />
                            <span>{choice}</span>
                          </label>
                        ))}
                      </div>
                    </div>
                  ),
                )}
              </div>
            ) : null}
            {many ? (
              <section className="mk-slot" aria-label="Meetings">
                {renderMeetingPicker ? (
                  renderMeetingPicker({ meetingIds, onChange: setMeetingIds })
                ) : (
                  <p className="field-hint">This recipe is made from several meetings. Right now it uses this one.</p>
                )}
              </section>
            ) : null}
            {recipe?.uses_brief && brief ? (
              brokenBrief ? (
                <p className="field-hint mk-brief">
                  <span className="mk-brief-name">{brief.team.name}</span> has no brief yet.{" "}
                  {brief.team.can_manage ? <AppLink href={teamLink}>Write one</AppLink> : null}
                </p>
              ) : (
                <label className="sh-check mk-brief">
                  <input
                    type="checkbox"
                    className="sh-box"
                    checked={useBrief}
                    onChange={(event) => setUseBrief(event.target.checked)}
                  />
                  <span>
                    Use the brief of <span className="mk-brief-name">{brief.team.name}</span>
                    <span className="field-hint"> {brief.text.length} characters</span>
                  </span>
                </label>
              )
            ) : null}
            {error ? (
              <Alert kind="error">
                <GateText message={error} />
              </Alert>
            ) : null}
          </div>
          <div className="sh-foot">
            <span className="sh-count" />
            <div className="sh-foot-actions">
              <Button kind="secondary" size="md" type="button" onClick={onClose}>
                Cancel
              </Button>
              <Button size="md" type="button" disabled={busy || !recipe} onClick={() => void submit()}>
                {busy ? "Making..." : "Make draft"}
              </Button>
            </div>
          </div>
        </>
      ) : null}
    </dialog>
  );
}
