"use client";

import { useEffect, useRef, useState } from "react";
import { Button, Icon } from "@/components/ui";

// Copies `text` to the clipboard. The Clipboard API needs a secure context (https, localhost);
// elsewhere a hidden textarea and execCommand do the job, as app/static/app.js did.
async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // fall through to the textarea
    }
  }
  const box = document.createElement("textarea");
  box.value = text;
  document.body.appendChild(box);
  box.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  box.remove();
  return ok;
}

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  async function copy() {
    setState((await copyText(text)) ? "done" : "failed");
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 2000);
  }

  return (
    <Button kind="secondary" size="sm" type="button" onClick={copy} aria-live="polite">
      <Icon name={state === "done" ? "check" : "copy"} size={14} />
      {state === "done" ? "Copied" : state === "failed" ? "Copy failed" : label}
    </Button>
  );
}
