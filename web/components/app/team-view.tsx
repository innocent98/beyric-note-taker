"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { TeamInviteDialog } from "@/components/app/team-invite-dialog";
import { MAX_NAME } from "@/components/app/teams-view";
import { Alert, AppLink, Button, Field, Icon, PageHead, Toast, Toggle } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { Team, TeamInvite, TeamMember, TeamMembers, TeamRole } from "@/lib/api/types";
import { initials, plural } from "@/lib/format";

export const INVITE_NAME_KEY = "beyric.invite-name";
const TONES = ["avatar-a", "avatar-b", "avatar-c", "avatar-d", "avatar-e"] as const;
const ROLE_LABEL: Record<TeamRole, string> = { owner: "Owner", admin: "Admin", member: "Member" };

// The Team screen (the designer's `isTeam`): the header with Invite people, the Members card (an
// avatar, the name with "You", the email, "Matched in meetings as ...", the role, "N open" or
// Pending, Resend and the remove button; invited people are rows too, with an Invited pill and
// Resend), the count line "N members, M pending", and the two cards "How tasks are assigned" and
// "Roles". `initial` is what the server read (null when it failed: "Could not load" and Try again).
//
// LEAD-DESIGNED, not in the designer's file (Adebayo approved 2026-11-05, to be confirmed by the
// designer): (1) the optional Name in the invite dialog; (2) editing "Matched in meetings as": an
// "Edit names" link opens one text field, names separated by commas; (3) the "Share new meetings"
// switch card (owner and admin only); (4) the inline question before removing someone or leaving,
// which says what happens, built from the Decisions "Remove" ask. The designer removes at once.
// The sentence "Owner manages billing" is trimmed: there is no billing (spec 2 row 1).
//
// LEAD-DESIGNED too (owner's request 2026-11-14, multi-team; for the designer to review), only while the
// server's multi-team switch is on: (5) the "Team name" card, for owners and admins of an ordinary team (a
// personal space keeps its name), and (6) the "All teams" and "Make a team" links above the heading: there is
// one Teams entry in the sidebar, so a team's page is reached from the Teams list and goes back to it.
export function TeamView({
  initialTeam,
  initialMembers,
  multiTeam = false,
}: {
  initialTeam: Team | null;
  initialMembers: TeamMembers | null;
  // The server's multi-team switch is on: the page names the team it is about, and a personal space
  // (nobody is invited into it) says so instead of offering "Invite people". Off: exactly as before.
  multiTeam?: boolean;
}) {
  const [team, setTeam] = useState(initialTeam);
  const [list, setList] = useState(initialMembers);
  const [failed, setFailed] = useState(initialTeam === null || initialMembers === null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteName, setInviteName] = useState("");
  const [removing, setRemoving] = useState<number | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, flash] = useToast();
  const router = useRouter();
  const [nameDraft, setNameDraft] = useState<string | null>(null);

  // "Invite them to the team" in the task drawer leaves the spoken name here and comes to this page.
  useEffect(() => {
    try {
      const spoken = window.sessionStorage.getItem(INVITE_NAME_KEY);
      if (spoken !== null) {
        window.sessionStorage.removeItem(INVITE_NAME_KEY);
        queueMicrotask(() => {
          setInviteName(spoken);
          setInviteOpen(true);
        });
      }
    } catch {
      // storage may be off; the dialog is one click away
    }
  }, []);

  async function reload() {
    setFailed(false);
    try {
      const [t, m] = await Promise.all([api.team.get(), api.team.members()]);
      setTeam(t);
      setList(m);
    } catch {
      setFailed(true);
    }
  }

  async function run<T>(key: string, work: () => Promise<T>, done?: (value: T) => void) {
    if (busy) return;
    setBusy(key);
    setError(null);
    try {
      done?.(await work());
    } catch (caught) {
      setError(errorMessage(caught));
    }
    setBusy(null);
  }

  if (failed || team === null || list === null) {
    return (
      <div className="stack-lg">
        <PageHead title="Team" subtitle="People on your team can be assigned tasks from meetings." />
        <Alert kind="error">
          <span>Could not load your team. </span>
          <button type="button" className="btn btn-link" onClick={() => void reload()}>
            Try again
          </button>
        </Alert>
      </div>
    );
  }

  const personal = multiTeam && team.is_personal;
  const subtitle = multiTeam && !team.is_personal ? `People on ${team.name} can be assigned tasks from meetings.` : "People on your team can be assigned tasks from meetings.";
  const pending = list.invites.length;
  const count = `${plural(list.members.length, "member")}, ${pending} pending`;

  async function refresh() {
    const [t, m] = await Promise.all([api.team.get(), api.team.members()]);
    setTeam(t);
    setList(m);
  }

  function saveAliases(member: TeamMember) {
    const names = draft.split(",").map((x) => x.trim()).filter(Boolean);
    void run(
      `aliases-${member.user_id}`,
      () => api.team.updateMember(member.user_id, { aliases: names }),
      (next) => {
        setList(next);
        setEditing(null);
      },
    );
  }

  const canRename = multiTeam && team.can_manage && !team.is_personal;
  const nameShown = nameDraft ?? team.name;
  const nameChanged = nameShown.trim() !== "" && nameShown.trim() !== team.name;

  return (
    <div className="stack-lg tm">
      {multiTeam ? (
        <nav className="tm-trail meta" aria-label="Teams">
          <AppLink href="/teams">&larr; All teams</AppLink>
          <span aria-hidden="true"> · </span>
          <AppLink href="/teams">Make a team</AppLink>
        </nav>
      ) : null}
      <PageHead title="Team" subtitle={subtitle}>
        {team.can_manage && !personal ? (
          <Button
            type="button"
            size="md"
            onClick={() => {
              setInviteName("");
              setInviteOpen(true);
            }}
          >
            <Icon name="plus" size={14} stroke={2.4} />
            Invite people
          </Button>
        ) : null}
      </PageHead>
      {error ? <Alert kind="error">{error}</Alert> : null}
      {personal ? (
        <Alert kind="info">
          This is your personal space. Only you are in it, and nobody can be invited into it. To work with other people,{" "}
          <AppLink href="/teams">make a team</AppLink>.
        </Alert>
      ) : null}
      {canRename ? (
        <section className="tm-card" aria-label="Team name">
          <form
            className="tm-name-form"
            onSubmit={(event) => {
              event.preventDefault();
              if (!nameChanged) return;
              void run(
                "rename",
                () => api.team.update({ name: nameShown.trim() }),
                (next) => {
                  setTeam(next);
                  setNameDraft(null);
                  flash("Team renamed");
                  // The switcher and the Teams list were drawn by the server: ask for them again.
                  router.refresh();
                },
              );
            }}
          >
            <Field
              id="team-rename"
              label="Team name"
              value={nameShown}
              maxLength={MAX_NAME}
              hint="Everyone on the team sees this name."
              onChange={(event) => setNameDraft(event.target.value)}
              disabled={busy !== null}
            />
            <Button size="md" disabled={busy !== null || !nameChanged}>
              {busy === "rename" ? "Saving..." : "Save name"}
            </Button>
          </form>
        </section>
      ) : null}
      <section className="tm-card" aria-label="Members">
        <div className="tm-card-head">
          <h2>Members</h2>
          <span>{count}</span>
        </div>
        <ul className="tm-list">
          {list.members.map((m, index) => (
            <li key={m.user_id}>
              <div className="tm-row">
                <div className={`avatar tm-avatar ${TONES[index % TONES.length]}`} aria-hidden="true">
                  {initials(m.name)}
                </div>
                <div className="tm-who">
                  <div className="tm-name">
                    <span>{m.name}</span>
                    {m.is_you ? <span className="tm-you">You</span> : null}
                  </div>
                  <div className="tm-email">{m.email}</div>
                  {editing === m.user_id ? (
                    <form
                      className="tm-alias-edit"
                      onSubmit={(event) => {
                        event.preventDefault();
                        saveAliases(m);
                      }}
                    >
                      <label className="visually-hidden" htmlFor={`aliases-${m.user_id}`}>
                        Names {m.name} is called in meetings
                      </label>
                      <input
                        id={`aliases-${m.user_id}`}
                        className="input"
                        value={draft}
                        autoFocus
                        placeholder="Dan, Danny (separate names with commas)"
                        onChange={(event) => setDraft(event.target.value)}
                      />
                      <Button size="sm" disabled={busy !== null}>
                        Save
                      </Button>
                      <Button kind="secondary" size="sm" type="button" onClick={() => setEditing(null)}>
                        Cancel
                      </Button>
                    </form>
                  ) : (
                    <div className="tm-alias">
                      {m.aliases.length > 0 ? <span>Matched in meetings as {m.aliases.join(", ")}</span> : null}
                      {m.can_edit_aliases ? (
                        <button
                          type="button"
                          className="tm-link"
                          onClick={() => {
                            setEditing(m.user_id);
                            setDraft(m.aliases.join(", "));
                          }}
                        >
                          {m.aliases.length > 0 ? "Edit names" : "Add names used in meetings"}
                        </button>
                      ) : null}
                    </div>
                  )}
                </div>
                <div className="tm-role">
                  {m.can_change_role ? (
                    <select
                      className="select tm-select"
                      aria-label={`Role of ${m.name}`}
                      value={m.role}
                      disabled={busy !== null}
                      onChange={(event) =>
                        void run(
                          `role-${m.user_id}`,
                          () => api.team.updateMember(m.user_id, { role: event.target.value as "admin" | "member" }),
                          setList,
                        )
                      }
                    >
                      <option value="admin">Admin</option>
                      <option value="member">Member</option>
                    </select>
                  ) : (
                    <span className="tm-role-text">{ROLE_LABEL[m.role]}</span>
                  )}
                </div>
                <div className="tm-open">{m.open_tasks} open</div>
                <div className="tm-actions">
                  {m.can_remove ? (
                    <button
                      type="button"
                      className="tm-x"
                      title={m.is_you ? "Leave team" : "Remove"}
                      aria-label={m.is_you ? "Leave team" : `Remove ${m.name}`}
                      onClick={() => setRemoving(m.user_id)}
                    >
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                        <path d="M6 6l12 12M18 6 6 18" />
                      </svg>
                    </button>
                  ) : null}
                </div>
              </div>
              {removing === m.user_id ? (
                <div className="meeting-delete-ask tm-ask" role="group" aria-label={m.is_you ? "Leave this team?" : `Remove ${m.name}?`}>
                  <p>
                    <strong>{m.is_you ? "Leave this team?" : `Remove ${m.name}?`}</strong>{" "}
                    {m.is_you ? "You" : "They"} will get a team of {m.is_you ? "your" : "their"} own and keep {m.is_you ? "your" : "their"} meetings.
                    Open tasks assigned to {m.is_you ? "you" : "them"} become unassigned, and meetings {m.is_you ? "you" : "they"} shared with
                    this team become private.
                  </p>
                  <div className="dec-ask-actions">
                    <Button kind="secondary" size="sm" type="button" onClick={() => setRemoving(null)}>
                      Cancel
                    </Button>
                    <Button
                      kind="danger"
                      size="sm"
                      type="button"
                      disabled={busy !== null}
                      onClick={() =>
                        void run(
                          `remove-${m.user_id}`,
                          async () => {
                            await api.team.removeMember(m.user_id);
                            // Leaving changes who the caller is on the team: read it all again.
                            await refresh();
                          },
                          () => {
                            setRemoving(null);
                            flash(m.is_you ? "You left the team" : `${m.name} was removed`);
                          },
                        )
                      }
                    >
                      {m.is_you ? "Leave team" : "Remove"}
                    </Button>
                  </div>
                </div>
              ) : null}
            </li>
          ))}
          {list.invites.map((invite) => (
            <InviteRow
              key={`invite-${invite.id}`}
              invite={invite}
              canManage={team.can_manage}
              busy={busy !== null}
              onResend={() =>
                void run(
                  `resend-${invite.id}`,
                  () => api.team.resend(invite.id),
                  () => {
                    flash(`Invite sent again to ${invite.email}`);
                    void refresh();
                  },
                )
              }
              onRevoke={() =>
                void run(
                  `revoke-${invite.id}`,
                  () => api.team.revoke(invite.id),
                  () => {
                    setList((current) => (current ? { ...current, invites: current.invites.filter((i) => i.id !== invite.id) } : current));
                    flash(`Invitation to ${invite.email} cancelled`);
                  },
                )
              }
            />
          ))}
        </ul>
      </section>
      <div className="tm-info">
        <section className="tm-info-card">
          <h2>How tasks are assigned</h2>
          <p>
            After each meeting, every action item becomes a task for the teammate named as its owner. Names are matched
            against each person&apos;s name and the names they appear as in meetings. Items with no match wait under Needs owner.
          </p>
        </section>
        <section className="tm-info-card tm-roles">
          <h2>Roles</h2>
          <div>
            <strong>Owner</strong> manages settings and the team.
          </div>
          <div>
            <strong>Admin</strong> invites people and manages all tasks.
          </div>
          <div>
            <strong>Member</strong> sees shared meetings and works on their tasks.
          </div>
        </section>
      </div>
      {team.can_manage ? (
        <section className="tm-card" aria-label="Sharing">
          <div className="pl-row pl-row-last pl-row-center">
            <div>
              <div className="pl-row-title" id="tm-share-label">
                Share new meetings with the team
              </div>
              <div className="pl-row-sub" id="tm-share-sub">
                Meetings recorded by owners and admins are shared when their notes are ready. Members share their own
                meetings from the meeting page. Meetings you shared before stay as they are.
                {multiTeam ? " Someone who chose a team under Settings, Share my new meetings with, follows that choice instead." : ""}
              </div>
            </div>
            <Toggle
              checked={team.share_new_meetings}
              disabled={busy !== null}
              aria-labelledby="tm-share-label"
              aria-describedby="tm-share-sub"
              onClick={() =>
                void run("share-default", () => api.team.update({ share_new_meetings: !team.share_new_meetings }), setTeam)
              }
            />
          </div>
        </section>
      ) : null}
      <TeamInviteDialog
        open={inviteOpen}
        canInviteAdmin={team.can_change_roles}
        initialName={inviteName}
        onClose={() => setInviteOpen(false)}
        onSent={(results) => {
          flash(results.length === 1 ? `Invite sent to ${results[0].email}` : `${results.length} invites sent`);
          void refresh();
        }}
      />
      <Toast message={toast} />
    </div>
  );
}

function InviteRow({
  invite,
  canManage,
  busy,
  onResend,
  onRevoke,
}: {
  invite: TeamInvite;
  canManage: boolean;
  busy: boolean;
  onResend: () => void;
  onRevoke: () => void;
}) {
  const label = invite.name || invite.email;
  return (
    <li>
      <div className="tm-row">
        <div className="avatar tm-avatar avatar-e" aria-hidden="true">
          {initials(label)}
        </div>
        <div className="tm-who">
          <div className="tm-name">
            <span>{label}</span>
          </div>
          <div className="tm-email">{invite.email}</div>
        </div>
        <div className="tm-role">
          <span className="tm-invited">{invite.status === "expired" ? "Expired" : "Invited"}</span>
        </div>
        <div className="tm-open">Pending</div>
        <div className="tm-actions">
          {canManage ? (
            <>
              <button type="button" className="tm-link tm-resend" disabled={busy} onClick={onResend}>
                Resend
              </button>
              <button type="button" className="tm-x" title="Cancel invitation" aria-label={`Cancel invitation to ${invite.email}`} disabled={busy} onClick={onRevoke}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                  <path d="M6 6l12 12M18 6 6 18" />
                </svg>
              </button>
            </>
          ) : null}
        </div>
      </div>
    </li>
  );
}
