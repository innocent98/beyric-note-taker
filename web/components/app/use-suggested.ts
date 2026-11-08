"use client";

import { useState } from "react";
import { api } from "@/lib/api/browser";
import { ApiError } from "@/lib/api/client";
import type { Task } from "@/lib/api/types";

// What a failed Accept or Dismiss says (lead-designed: the designer's file draws no failure). The row
// stays where it was, so the person can try again.
export const SUGGESTION_FAILED = "Could not update the task. Try again.";

// Accept and Dismiss of a suggested task (docs/fe-integration-guide-suggested-tasks.md), shared by the
// Tasks screen, the task drawer and the meeting page's Action items. The toasts are the designer's:
// "Task added to the board" and "Suggestion dismissed". A second click while one is going is ignored.
// 404 means the task is gone for this person (dismissed elsewhere): the toast says it failed and the
// row is dropped. Accepting a task already accepted answers 200 and is drawn as accepted.
export function useSuggestedActions({
  flash,
  onAccepted,
  onDismissed,
}: {
  flash: (message: string) => void;
  onAccepted: (task: Task) => void;
  onDismissed: (id: number) => void;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);

  async function run(task: Task, act: () => Promise<void>) {
    if (busyId !== null) return;
    setBusyId(task.id);
    try {
      await act();
    } catch (caught) {
      flash(SUGGESTION_FAILED);
      if (caught instanceof ApiError && caught.status === 404) onDismissed(task.id);
    }
    setBusyId(null);
  }

  return {
    busyId,
    accept: (task: Task) =>
      run(task, async () => {
        onAccepted(await api.tasks.accept(task.id));
        flash("Task added to the board");
      }),
    dismiss: (task: Task) =>
      run(task, async () => {
        await api.tasks.remove(task.id);
        onDismissed(task.id);
        flash("Suggestion dismissed");
      }),
  };
}
