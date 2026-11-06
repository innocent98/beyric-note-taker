"use client";

import { useState } from "react";
import { api } from "@/lib/api/browser";
import type { OpenQuestion } from "@/lib/api/types";

// The meeting page's "Open questions" of a recurring meeting (the designer's `questions` block with
// `carriable`): each question with a small pill that says where it stands: "Add to next agenda",
// "On next agenda" or "Resolved" (docs/fe-integration-guide-series.md, PATCH /api/questions/{id}).
// Clicking carries it or stops carrying it, and reopens a resolved one, as the prototype's toggle
// does. The pill is switched off for someone who may read the meeting but not change it
// (`can_update`). A failed save leaves the pill where it was and says so. Plain text throughout.
export function OpenQuestions({ initial, heading }: { initial: OpenQuestion[]; heading: string }) {
  const [questions, setQuestions] = useState(initial);
  const [failed, setFailed] = useState<number | null>(null);

  async function toggle(question: OpenQuestion) {
    setFailed(null);
    try {
      const next = await api.questions.update(question.id, { carry: question.resolved || !question.carry });
      setQuestions((all) => all.map((q) => (q.id === next.id ? next : q)));
    } catch {
      setFailed(question.id);
    }
  }

  return (
    <>
      <h2>{heading}</h2>
      <ul>
        {questions.map((question) => {
          const on = question.carry && !question.resolved;
          const label = question.resolved ? "Resolved" : question.carry ? "On next agenda" : "Add to next agenda";
          return (
            <li key={question.id} className="oq-item">
              <span className="oq-text">{question.text}</span>
              {question.carriable ? (
                <button
                  type="button"
                  className={on ? "oq-pill oq-pill-on" : "oq-pill"}
                  disabled={!question.can_update}
                  title={question.can_update ? undefined : "Only the person who recorded the meeting, or an owner or admin, can change this."}
                  onClick={() => void toggle(question)}
                >
                  {label}
                </button>
              ) : null}
              {failed === question.id ? (
                <span className="oq-failed" role="alert">
                  Could not save. Try again.
                </span>
              ) : null}
            </li>
          );
        })}
      </ul>
    </>
  );
}
