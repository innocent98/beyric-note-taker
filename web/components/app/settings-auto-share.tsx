"use client";

import { useRef, useState } from "react";
import { Alert, AppLink, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { TeamSummary } from "@/lib/api/types";

export const NO_TEAM = "none";

// What the page says when saving is refused. The codes and strings are the guide's (section 12):
// 409 `personal_team` and `multi_team_disabled` carry their own words; 404 "No such team." is not shown
// by `errorMessage`, so it is spelled out.
export function autoShareErrorMessage(caught: unknown): string {
  if (caught instanceof ApiError) {
    if (caught.code === "personal_team" || caught.code === "multi_team_disabled") return caught.message;
    if (caught.status === 404 && caught.message === "No such team.") return "You are no longer in that team. Choose another.";
  }
  return errorMessage(caught);
}

// LEAD-DESIGNED, not in the designer's file (owner's request 2026-11-14, multi-team stage 3; for the
// designer to review): "Share my new meetings with", the per-person auto-share team of stage 2b
// (GET and PUT /api/settings/auto-share). Built from the Settings language card: a title, one line, one
// select, saved at once and answered with a toast. Shown only while the server's multi-team switch is on.
//
// How it meets the team-wide switch on the Team screen ("Share new meetings with the team", owners and
// admins): a person who chose a team here is shared to THAT team, whatever their role in it, and the
// team-wide switch no longer applies to their meetings (`teams.apply_share_default`); with "No team" the
// team-wide switch applies as before. The card says so in one line.
export function SettingsAutoShare({ initial, teams }: { initial: number | null; teams: TeamSummary[] }) {
  const options = teams.filter((t) => !t.is_personal);
  // A choice the person is no longer in reads as none (the API does the same).
  const known = (id: number | null) => (id !== null && options.some((t) => t.id === id) ? id : null);
  const [value, setValue] = useState<number | null>(known(initial));
  const [error, setError] = useState<string | null>(null);
  const [toast, flash] = useToast();
  const latest = useRef(0);

  async function change(raw: string) {
    const next = raw === NO_TEAM ? null : Number(raw);
    const before = value;
    const mine = ++latest.current;
    setValue(next);
    setError(null);
    try {
      await api.settings.autoShare.set(next);
      if (latest.current === mine) {
        flash(next === null ? "New meetings will stay private" : `New meetings will be shared with ${options.find((t) => t.id === next)?.name}`);
      }
    } catch (caught) {
      if (latest.current !== mine) return;
      setValue(before);
      setError(autoShareErrorMessage(caught));
    }
  }

  return (
    <section className="card pl-language" aria-labelledby="set-auto-share">
      <div className="pl-language-text">
        <h2 id="set-auto-share" className="set-title">
          Share my new meetings with
        </h2>
        <p className="set-sub">
          New meetings are shared with this team when their notes are ready. Never when the bot joins, and never again
          for a meeting you unshared.
        </p>
        <p className="set-sub">
          This is your choice for your own meetings. It is used instead of the team-wide &quot;Share new meetings with the
          team&quot; switch on the Team screen; with &quot;No team&quot; that switch applies as before.
        </p>
      </div>
      <div className="pl-language-control">
        {options.length > 0 ? (
          <select
            className="select pl-s36"
            aria-label="Share my new meetings with"
            value={value === null ? NO_TEAM : String(value)}
            onChange={(event) => void change(event.target.value)}
          >
            <option value={NO_TEAM}>No team (keep private)</option>
            {options.map((team) => (
              <option key={team.id} value={team.id}>
                {team.name}
              </option>
            ))}
          </select>
        ) : (
          <p className="set-sub">
            You are not in a team yet. <AppLink href="/teams">Make a team</AppLink> to share new meetings with it.
          </p>
        )}
        {error ? <Alert kind="error">{error}</Alert> : null}
      </div>
      <Toast message={toast} />
    </section>
  );
}
