"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useActiveTeam } from "@/components/app/use-active-team";
import { Alert, AppLink, Badge, Button, Card, Checkbox, EmptyState, Field, ListRow, PageHead, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { withTeam, writeStoredTeam } from "@/lib/active-team";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { TeamList, TeamRole, TeamSummary } from "@/lib/api/types";
import { BILLING_PLANS_HREF } from "@/lib/billing";
import { plural } from "@/lib/format";
import { workspaceWords } from "@/lib/workspace-words";

// A class says Lecturer and Student where a team says Owner and Member (`workspaceWords`).
export function roleLabel(role: TeamRole, kind: TeamSummary["kind"]): string {
  const words = workspaceWords(kind);
  return role === "owner" ? words.owner : role === "member" ? words.member : "Admin";
}
export const MAX_NAME = 100;

// The words of the refusals, from docs/fe-integration-guide-teams.md section 9. 422 is not shown by
// `errorMessage` (developer text elsewhere), but `invalid_name` is written for the person.
const OWN_WORDS = new Set(["team_limit", "invalid_name", "multi_team_disabled", "personal_team"]);
export function createErrorMessage(caught: unknown): string {
  if (caught instanceof ApiError && OWN_WORDS.has(caught.code)) return caught.message;
  return errorMessage(caught);
}

// LEAD-DESIGNED, not in the designer's file (owner's request 2026-11-14, multi-team stage 3; for the
// designer to review): the Teams screen. A list of the person's teams (name, "Personal" or role, how many
// people) with an Open button that makes it this tab's team and goes to its Team page, and a "Make a team"
// card with one name field. Built from the app's own list rows, cards, badges and field. Shown only while
// the server's multi-team switch is on.
//
// LEAD-DESIGNED too (workspaces W4, for the designer to review): the plan's allowance on that card ("You own
// 1 of 3 teams your plan allows", or "Your plan has no limit"), the plain upgrade message with a "See plans"
// link at the limit (the server's own sentence, `limit_message`), and the "This is a class" tick, which
// makes a class: the same workspace in a school's words, sharing every meeting with its members by default.
export function TeamsView({ initial }: { initial: TeamList | null }) {
  const router = useRouter();
  const [list, setList] = useState(initial);
  const [name, setName] = useState("");
  const [isClass, setIsClass] = useState(false);
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
      await api.teams.create(name, isClass ? "class" : "team");
      setName("");
      setIsClass(false);
      flash(isClass ? "Class made" : "Team made");
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

  // `can_create_more` is null on a plan with no limit; 0 is the limit reached (Free included).
  const full = list.can_create_more === 0;
  const allowance =
    list.max_owned_teams === null
      ? `You own ${list.owned_teams}. Your plan has no limit.`
      : `You own ${list.owned_teams} of the ${plural(list.max_owned_teams, "team")} your plan allows. You can make ${list.can_create_more} more.`;
  return (
    <div className="stack-md measure teams">
      <PageHead title="Teams" subtitle="The teams you belong to. The switcher chooses which one the Meetings, Tasks, Projects, Decisions, Review and Settings screens work on." />
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
                  {team.is_personal ? <Badge>Personal</Badge> : <Badge tone="info">{roleLabel(team.role, team.kind)}</Badge>}
                  {team.kind === "class" ? <Badge tone="success">Class</Badge> : null}
                  {team.id === activeId ? <Badge tone="brand">Working in this team</Badge> : null}
                  <span className="meta">{team.is_personal ? "Only you" : plural(team.member_count, workspaceWords(team.kind).member.toLowerCase())}</span>
                </span>
              </ListRow>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Make a team">
        <p className="meta">A team can be just you. Teams can share meetings and connect Slack and Linear.</p>
        {full ? (
          <Alert kind="notice">
            <span>{list.limit_message ?? "You have reached the number of teams your plan allows."} </span>
            <AppLink href={BILLING_PLANS_HREF}>See plans</AppLink>
          </Alert>
        ) : null}
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
            hint={allowance}
            onChange={(event) => setName(event.target.value)}
          />
          <Checkbox id="team-class" checked={isClass} disabled={full} onChange={(event) => setIsClass(event.target.checked)}>
            This is a class. Students join as members, and every meeting of the class is shared with them by default.
          </Checkbox>
          <div>
            <Button size="md" disabled={busy || full || name.trim() === ""}>
              {busy ? "Making..." : isClass ? "Make class" : "Make team"}
            </Button>
          </div>
        </form>
      </Card>
      <Toast message={toast} />
    </div>
  );
}
