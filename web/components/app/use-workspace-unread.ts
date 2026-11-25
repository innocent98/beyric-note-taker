"use client";

import { useEffect, useState } from "react";
import { TEAM_EVENT } from "@/lib/active-team";
import { api } from "@/lib/api/browser";

// The unread notification count of every workspace OTHER than the active one, for the switcher's dots (workspaces
// design, section 3: nothing in another workspace is missed). One read of `GET /api/session` (`other_workspaces`),
// made when the switcher appears and again when this tab changes workspace. A failure (offline, signed out) draws
// no dot: the switcher works without it.
export function useWorkspaceUnread(): Record<number, number> {
  const [unread, setUnread] = useState<Record<number, number>>({});
  useEffect(() => {
    let live = true;
    const read = () => {
      api
        .session()
        .then((state) => {
          if (!live) return;
          const next: Record<number, number> = {};
          for (const other of state.other_workspaces ?? []) next[other.id] = other.unread_notifications;
          setUnread(next);
        })
        .catch(() => undefined);
    };
    read();
    window.addEventListener(TEAM_EVENT, read);
    return () => {
      live = false;
      window.removeEventListener(TEAM_EVENT, read);
    };
  }, []);
  return unread;
}
