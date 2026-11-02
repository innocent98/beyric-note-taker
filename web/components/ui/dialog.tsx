import type { ReactNode } from "react";

// A native <dialog>. Open it with a button carrying data-dialog-open="<id>", close it with one
// carrying data-dialog-close; components/behaviors.tsx wires both.
export function Dialog({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <dialog className="dialog" id={id} aria-labelledby={`${id}-title`}>
      <h2 className="dialog-title" id={`${id}-title`}>
        {title}
      </h2>
      {children}
    </dialog>
  );
}
