import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { SetupWorkspace } from "@/components/auth/setup-workspace";
import { AuthShell } from "@/components/site/auth-shell";
import { ApiError } from "@/lib/api/client";
import type { CalendarState } from "@/lib/api/types";
import { requireOwner } from "@/lib/api/server";

export const metadata: Metadata = { title: "Set up your workspace", robots: { index: false, follow: false } };

// The designer's "Set up your workspace" step (Auth.dc.html:142-169), offered after a new account's
// first sign-in (login-form.tsx). Every account already has a team of one, called "My team", so this
// step only renames it (PATCH /api/team, owner or admin) and offers the calendar connection. It is
// never a gate: "Skip for now" leads to the app, and a person who cannot rename the team (a member
// who joined by invitation) is sent straight there.
export default async function SetupPage() {
  const api = await requireOwner();
  const team = await api.team.get().catch((error: unknown) => {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
    return null;
  });
  if (!team?.can_manage) redirect("/");
  // The calendar block is left out while the server has the feature off (404 `calendar_unavailable`).
  const calendar: CalendarState | null = await api.calendar.get().catch(() => null);
  return (
    <AuthShell>
      <SetupWorkspace
        calendarConnected={calendar?.connected ?? null}
        microsoftEnabled={calendar?.microsoft_enabled ?? false}
        connectedProvider={calendar?.provider ?? null}
      />
    </AuthShell>
  );
}
