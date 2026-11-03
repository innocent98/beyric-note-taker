"use client";

import { useState } from "react";

// Tabs that switch a panel on the same page (the meeting's Notes and Transcript); the parent
// shows the panel for the id `onChange` reports. Use Tabs when each tab is its own page.
export function TabButtons({
  items,
  defaultId,
  onChange,
  label,
}: {
  items: ReadonlyArray<{ id: string; label: string }>;
  defaultId: string;
  onChange?: (id: string) => void;
  label: string;
}) {
  const [current, setCurrent] = useState(defaultId);
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {items.map((item) => (
        <button
          key={item.id}
          className="tab"
          type="button"
          role="tab"
          id={`tab-${item.id}`}
          aria-selected={item.id === current}
          aria-controls={`panel-${item.id}`}
          onClick={() => {
            setCurrent(item.id);
            onChange?.(item.id);
          }}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
