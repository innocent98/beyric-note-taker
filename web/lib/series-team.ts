import type { SeriesPage } from "@/lib/api/types";

// Which team a recurring row belongs to, in words (docs/fe-integration-guide-series-teams.md). LEAD-DESIGNED,
// not in the designer's files: they drew no team on a Recurring card or the Series page, because before
// multi-team there was one team. A row is one team's, or one person's personal space; its `team` is on the
// card and on the page, always, and the name of a personal space is "My team" (the API's default), so a
// personal row is told by `is_personal` and called "Personal". Shown only while the server's multi-team
// switch is on (`GET /api/session` `multi_team`); with it off the pages are as they were.
export type RowTeam = SeriesPage["team"];

export function teamLabel(team: RowTeam): string {
  return team.is_personal ? "Personal" : team.name;
}

// The line under the title of the Series page: what this page lists, so a Standup that shows fewer
// meetings than the calendar has is not a surprise.
export function scopeNote(team: RowTeam): string {
  return team.is_personal
    ? "Your private meetings of this series. The ones you share with a team are on that team's page."
    : `The meetings of this series that are shared with ${team.name}.`;
}

// What the "Past meetings" column says when the row has no meeting at all (a row can: the person shared
// or moved its last meeting away, and the empty row stays until they record another).
export function noPastText(team: RowTeam | null): string {
  if (team === null) return "No meetings recorded in this series yet.";
  return team.is_personal
    ? "None of your private meetings are in this series right now."
    : `No meeting of this series is shared with ${team.name} right now.`;
}
