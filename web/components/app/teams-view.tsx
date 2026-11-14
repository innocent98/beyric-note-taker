"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useActiveTeam } from "@/components/app/use-active-team";
import { Alert, Badge, Button, Card, EmptyState, Field, ListRow, PageHead, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { withTeam, writeStoredTeam } from "@/lib/active-team";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { TeamList, TeamRole, TeamSummary } from "@/lib/api/types";
import { plural } from "@/lib/format";

const ROLE_LABEL: Record<TeamRole, string> = { owner: "Owner", admin: "Admin", member: "Member" };
export const MAX_NAME = 100;

// The words of the refusals, from docs/fe-integration-guide-teams.md section 9. 422 is not shown by
// `errorMessage` (developer text elsewhere), but `invalid_name` is written for the person.
const OWN_WORDS = new Set(["team_limit", "invalid_name", "multi_team_disabled"]);
export function createErrorMessage(caught: unknown): string {
  if (caught instanceof ApiError && OWN_WORDS.has(caught.code)) return caught.message;
  return errorMessage(caught);
}

// LEAD-DESIGNED, not in the designer's file (owner's request 2026-11-14, multi-team stage 3; for the
// designer to review): the Teams screen. A list of the person's teams (name, "Personal" or role, how many
// people) with an Open button that makes it this tab's team and goes to its Team page, and a "Make a team"
// card with one name field. Built from the app's own list rows, cards, badges and field. Shown only while
// the server's multi-team switch is on.
export function TeamsView({ initial }: { initial: TeamList | null }) {
  const router = useRouter();
  const [list, setList] = useState(initial);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, flash] = useToast(2600);
  const { activeId } = useActiveTeam(list?.teams ?? []);

  async function reload() {
    try {
      setList(await api.teams.list());
    } catch {
      // Keep what is on screen.
    }
  }

  async function create() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.teams.create(name);
      setName("");
      flash("Team made");
      await reload();
      // The switcher in the shell was drawn by the server: ask for it again.
      router.refresh();
    } catch (caught) {
      setError(createErrorMessage(caught));
      // The limit may have been reached from another tab: show the truth.
      if (caught instanceof ApiError && caught.code === "team_limit") void reload();
    }
    setBusy(false);
  }

  function open(team: TeamSummary) {
    // Remember it for this tab (the default team is "no choice"); the link carries it to the Team page.
    writeStoredTeam(team.is_default ? null : team.id);
  }

  if (list === null) {
    return (
      <div className="stack-lg">
        <PageHead title="Teams" subtitle="The teams you belong to." />
        <Alert kind="error">
          <span>Could not load your teams. </span>
          <button type="button" className="btn btn-link" onClick={() => void reload()}>
            Try again
          </button>
        </Alert>
      </div>
    );
  }

  const full = list.can_create_more === 0;
  return (
    <div className="stack-md measure teams">
      <PageHead title="Teams" subtitle="The teams you belong to. The switcher chooses which one the Team, Tasks, Projects and Settings screens work on." />
      {error ? <Alert kind="error">{error}</Alert> : null}
      <Card flush>
        {list.teams.length === 0 ? (
          <EmptyState title="No teams yet" />
        ) : (
          <ul className="list" aria-label="Your teams">
            {list.teams.map((team) => (
              <ListRow
                key={team.id}
                end={
                  <Button
                    kind="secondary"
                    size="sm"
                    href={withTeam("/team", team.is_default ? null : team.id)}
                    aria-label={`Open ${team.name}`}
                    onClick={() => open(team)}
                  >
                    Open
                  </Button>
                }
              >
                <span className="teams-name">{team.name}</span>
                <span className="teams-meta">
                  {team.is_personal ? <Badge>Personal</Badge> : <Badge tone="info">{ROLE_LABEL[team.role]}</Badge>}
                  {team.id === activeId ? <Badge tone="brand">Working in this team</Badge> : null}
                  <span className="meta">{team.is_personal ? "Only you" : plural(team.member_count, "member")}</span>
                </span>
              </ListRow>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Make a team">
        <form
          className="stack-sm"
          onSubmit={(event) => {
            event.preventDefault();
            void create();
          }}
        >
          <Field
            id="team-name"
            label="Team name"
            value={name}
            maxLength={MAX_NAME}
            disabled={full}
            hint={
              full
                ? "You already own 3 teams, the most one account can have."
                : `You can own up to 3 teams. You can make ${list.can_create_more} more.`
            }
            onChange={(event) => setName(event.target.value)}
          />
          <div>
            <Button size="md" disabled={busy || full || name.trim() === ""}>
              {busy ? "Making..." : "Make team"}
            </Button>
          </div>
        </form>
      </Card>
      <Toast message={toast} />
    </div>
  );
}
