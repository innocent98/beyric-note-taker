import type { TeamSummary } from "@/lib/api/types";

// The words a workspace is called in. A class is an ordinary team workspace in a school's words
// (workspaces design, section 6): the same screens and rules, only the nouns change. ONE helper, so a
// screen never decides on its own what to call a class. `kind` is the server's `team` or `class`
// (`TeamSummary.kind`, `Team.kind`); anything else, and a missing value from an older response, reads
// as a team.
export type WorkspaceKind = TeamSummary["kind"];

export type WorkspaceWords = {
  // "team" / "class": lower case, for a sentence ("Leave this class?").
  noun: string;
  // "Team" / "Class": the label of the workspace (the switcher's "(class)" tag, the page title).
  label: string;
  // The people in it: "Members" / "Students".
  members: string;
  member: string;
  // The person who owns it: "Owner" / "Lecturer".
  owner: string;
};

const TEAM: WorkspaceWords = { noun: "team", label: "Team", members: "Members", member: "Member", owner: "Owner" };
const CLASS: WorkspaceWords = { noun: "class", label: "Class", members: "Students", member: "Student", owner: "Lecturer" };

export function workspaceWords(kind: WorkspaceKind | string | null | undefined): WorkspaceWords {
  return kind === "class" ? CLASS : TEAM;
}
