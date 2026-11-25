"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api/browser";
import { ApiError } from "@/lib/api/client";
import type { DocumentDraft } from "@/lib/api/types";

// How often to ask while a draft is being written (docs/fe-integration-guide-recipes.md): the polling
// shape of `useTranslation`, every 2 seconds, until the draft is ready or failed.
export const DOCUMENT_POLL_MS = 2_000;

export type DocumentState = {
  doc: DocumentDraft;
  // The draft was discarded (or never was ours) while this page was open: the poll answered 404.
  gone: boolean;
  // Take the answer of a save: the draft as the server now holds it.
  replace: (next: DocumentDraft) => void;
};

export function useDocument(initial: DocumentDraft): DocumentState {
  const [doc, setDoc] = useState(initial);
  const [gone, setGone] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  useEffect(() => {
    if (doc.status !== "writing") return;
    const timer = setInterval(() => {
      void api.documents
        .get(doc.id)
        .then((next) => {
          if (alive.current) setDoc(next);
        })
        .catch((caught) => {
          if (alive.current && caught instanceof ApiError && caught.status === 404) setGone(true);
          // Anything else keeps asking: a lost connection is not a failed draft.
        });
    }, DOCUMENT_POLL_MS);
    return () => clearInterval(timer);
  }, [doc.id, doc.status]);

  const replace = useCallback((next: DocumentDraft) => setDoc(next), []);
  return { doc, gone, replace };
}
