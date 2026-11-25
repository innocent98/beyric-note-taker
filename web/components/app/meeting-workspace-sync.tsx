"use client";

import { useEffect } from "react";
import { Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { activeTeamForRequest, planMeetingSwitch, shellDefaultTeam, writeStoredTeam } from "@/lib/active-team";
import type { TeamSummary } from "@/lib/api/types";

// LEAD-DESIGNED, not in the designer's file (workspaces design 12.2; for the designer to review): a meeting is
// opened by its id from any workspace (a link, an email, a notification, an Ask source chip). When the meeting
// lives in another workspace than this tab is on, the tab switches to it (the switcher follows, the next
// workspace-scoped call names it) and the toast says "Switched to <name>", in the designer's own toast. Draws nothing
// else. Runs once when the page opens.
export function MeetingWorkspaceSync({ workspace, teams }: { workspace: { id: number; name: string } | null; teams: TeamSummary[] | null }) {
  const [toast, flash] = useToast(4000);
  useEffect(() => {
    if (!teams) return;
    const step = planMeetingSwitch({ workspace, activeId: activeTeamForRequest(), defaultId: shellDefaultTeam(), teams });
    if (step.kind !== "switch") return;
    writeStoredTeam(step.remember);
    flash(step.message);
    // Once per page: the dependencies are what the server rendered.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return <Toast message={toast} />;
}
