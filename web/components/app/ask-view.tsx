"use client";

import { useRef, useState, type FormEvent } from "react";
import { AppLink, Button, Icon } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError } from "@/lib/api/client";
import type { AskAnswer, AskScope } from "@/lib/api/types";
import { ASK_CHIP_FLAG, citeHref, citeLabel } from "@/lib/ask";
import { plural } from "@/lib/format";

// The longest question the API takes (spec 1.4: 300 characters; longer is a 422).
const MAX_QUESTION = 300;

// What a card shows. `failed` can be asked again; `refused` (the daily cap, a question the API
// will not take) says its message and offers nothing to press.
type Card =
  | { id: number; question: string; state: "loading" }
  | { id: number; question: string; state: "ready"; answer: AskAnswer }
  | { id: number; question: string; state: "failed" }
  | { id: number; question: string; state: "refused"; message: string };

// Ask Beyric (the designer's screen, D:564-608): a question box, the suggestion chips, the scope
// line and the thread of answers, newest first, kept in page state only (a refresh clears it; spec
// 1.8 item 5). Every point carries source chips that open the meeting, at the cited row of the
// transcript when there is one. `scope` is null when the first load failed: the box still works,
// the chips and the scope line are left out. `initials` is the person's own, for the question row.
//
// Lead-designed, not in the designer's file (docs/sop/2026-11-08-ask-beyric-screens.md): the
// no-meetings line, the failure card with Try again, the cap message, "Answers can be wrong. Check
// the sources.", the owner on a teammate's chip, and the single-column phone layout.
export function AskView({ scope, initials }: { scope: AskScope | null; initials: string }) {
  const [input, setInput] = useState("");
  const [thread, setThread] = useState<Card[]>([]);
  const nextId = useRef(1);

  function update(id: number, card: Card) {
    setThread((current) => current.map((c) => (c.id === id ? card : c)));
  }

  async function run(id: number, question: string) {
    try {
      update(id, { id, question, state: "ready", answer: await api.ask.ask(question) });
    } catch (caught) {
      if (caught instanceof ApiError && (caught.status === 429 || caught.status === 422)) {
        update(id, { id, question, state: "refused", message: caught.message });
      } else {
        // A failed call and a lost connection read the same: Beyric could not answer.
        update(id, { id, question, state: "failed" });
      }
    }
  }

  function ask(text: string) {
    const question = text.trim();
    if (!question) return;
    const id = nextId.current++;
    setInput("");
    setThread((current) => [{ id, question, state: "loading" }, ...current]);
    void run(id, question);
  }

  function retry(card: Card) {
    update(card.id, { id: card.id, question: card.question, state: "loading" });
    void run(card.id, card.question);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    ask(input);
  }

  return (
    <div className="ask">
      <div className="ask-head">
        <h1>Ask Beyric</h1>
        <p>Ask about anything said, decided or assigned in your meetings. Every answer links to the moment it came from.</p>
      </div>
      <form className="ask-box" role="search" onSubmit={submit}>
        <svg className="ask-glass" width="18" height="18" viewBox="0 0 24 24" fill="none" strokeWidth="2" strokeLinecap="round" aria-hidden="true" focusable="false">
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.5-3.5" />
        </svg>
        <input
          className="ask-input"
          type="text"
          aria-label="Your question"
          placeholder="e.g. What did we decide about pricing?"
          autoComplete="off"
          maxLength={MAX_QUESTION}
          value={input}
          onChange={(event) => setInput(event.target.value)}
        />
        <button className="ask-go" type="submit">
          Ask
        </button>
      </form>
      {scope && scope.suggestions.length > 0 ? (
        <div className="ask-chips">
          {scope.suggestions.map((text) => (
            <button className="ask-chip" type="button" key={text} onClick={() => ask(text)}>
              {text}
            </button>
          ))}
        </div>
      ) : null}
      {scope ? (
        <>
          <div className="ask-scope">
            Searches {plural(scope.meetings, "meeting")}, {plural(scope.decisions, "decision")} and {plural(scope.tasks, "task")}
          </div>
          {scope.meetings === 0 ? <div className="ask-scope">Nothing to search yet. Meetings appear here after they end.</div> : null}
        </>
      ) : null}
      {thread.map((card) => (
        <section className="ask-card" key={card.id}>
          <div className="ask-q">
            <span className="ask-me" aria-hidden="true">
              {initials}
            </span>
            <h2>{card.question}</h2>
          </div>
          <div className="ask-body">
            {card.state === "loading" ? (
              <div className="ask-wait" role="status">
                <Icon name="spinner" />
                Searching your meetings...
              </div>
            ) : card.state === "ready" ? (
              <Answer answer={card.answer} />
            ) : card.state === "failed" ? (
              <div className="state-fail" role="alert">
                <span className="state-fail-text">
                  <Icon name="info" />
                  Beyric could not answer that.
                </span>
                <Button kind="secondary" size="sm" type="button" onClick={() => retry(card)}>
                  Try again
                </Button>
              </div>
            ) : (
              <div className="state-fail" role="alert">
                <span className="state-fail-text">
                  <Icon name="info" />
                  {card.message}
                </span>
              </div>
            )}
          </div>
        </section>
      ))}
    </div>
  );
}

function Answer({ answer }: { answer: AskAnswer }) {
  const none = answer.kind === "none";
  return (
    <div className="ask-answer">
      <p className="ask-lead">{answer.lead}</p>
      {answer.points.length > 0 ? (
        <ul className="ask-points">
          {answer.points.map((point, index) => (
            <li className="ask-point" key={index}>
              <span className="ask-dot" aria-hidden="true" />
              <div className="ask-point-body">
                <span className="ask-point-text">{point.text}</span>
                {point.cites.length > 0 ? (
                <div className="ask-cites">
                  {point.cites.map((cite, i) => (
                    <AppLink
                      className="ask-cite"
                      href={citeHref(cite)}
                      key={`${cite.meeting_id}-${cite.line ?? "n"}-${i}`}
                      onClick={() => {
                        try {
                          sessionStorage.setItem(ASK_CHIP_FLAG, `/meetings/${cite.meeting_id}`);
                        } catch {
                          // Private mode: a missing meeting then shows the plain 404 page.
                        }
                      }}
                    >
                      <Icon name="video" size={12} />
                      {citeLabel(cite)}
                    </AppLink>
                  ))}
                </div>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      {none ? <div className="ask-hint">Try asking about a person, a project, a decision or a date.</div> : <div className="ask-caveat">Answers can be wrong. Check the sources.</div>}
    </div>
  );
}
