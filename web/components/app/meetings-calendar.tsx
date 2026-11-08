"use client";

import { useRouter } from "next/navigation";
import { Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import type { CalendarState, NotRecordedItem } from "@/lib/api/types";
import { CalendarBand } from "./calendar-upcoming";
import { NotRecorded } from "./not-recorded";
import { useSendNow } from "./use-send-now";

// The calendar part of the Meetings screen: the Upcoming list (or the Connect band) and, below it,
// Not recorded. They share one Send now (`useSendNow`) and one toast, and after the server accepts a
// send the page is read again so the new meeting, the lists and the rows all follow.
export function MeetingsCalendar({
  state,
  notRecorded,
  timeZone,
  now,
}: {
  state: CalendarState | null;
  notRecorded: NotRecordedItem[];
  timeZone?: string;
  now?: Date;
}) {
  const router = useRouter();
  const [toast, flash] = useToast(3200);
  const control = useSendNow({ flash, onChanged: () => router.refresh() });
  return (
    <>
      <CalendarBand state={state} timeZone={timeZone} now={now} sendNow={control} />
      <NotRecorded items={notRecorded} control={control} timeZone={timeZone} now={now} />
      <Toast message={toast} />
    </>
  );
}
