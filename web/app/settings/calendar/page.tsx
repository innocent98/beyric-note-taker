import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AppShell } from "@/components/app/shell";
import { CalendarSettings } from "@/components/app/calendar-settings";
import { calendarNotice } from "@/lib/calendar-notice";
import { ApiError } from "@/lib/api/client";
import { getReviewOpen, requireOwner } from "@/lib/api/server";
import type { CalendarState } from "@/lib/api/types";

export const metadata: Metadata = { title: "Calendar settings", robots: { index: false, follow: false } };

// The page Google's callback sends the browser back to (`?calendar=connected` or
// `?calendar_error=<code>`). While the server has the calendar off, GET /api/calendar answers 404
// `calendar_unavailable`: the page then says so instead of showing a not-found page, so this call
// is not wrapped in `load` (which turns every 404 into the not-found page).
export default async function CalendarSettingsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const api = await requireOwner();
  let state: CalendarState | null;
  try {
    state = await api.calendar.get();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/login");
    if (error instanceof ApiError && error.code === "calendar_unavailable") state = null;
    else throw error;
  }
  return (
    <AppShell current="settings" reviewOpen={await getReviewOpen()}>
      <CalendarSettings initial={state} notice={calendarNotice(await searchParams)} />
    </AppShell>
  );
}
