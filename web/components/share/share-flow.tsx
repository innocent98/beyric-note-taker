"use client";

import { useEffect, useRef, useState } from "react";
import { Alert, AppLink, Button, Card, Icon, SelectField } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { ApiError, errorMessage } from "@/lib/api/client";
import type { Meeting, Template } from "@/lib/api/types";
import { describeLink, type SharedLink } from "@/lib/share";
import { clearShare, peekShare, readShareFragment, stashShare } from "@/lib/share-pending";

// What /share shows after another app shared something with this one. The owner always taps
// "Send note taker" once: a link someone else made (a page, a message) must not start a recording
// by itself, and the tap is also where the "tell participants" reminder sits. The send is the same
// call as the Meetings page's form (POST /api/meetings through the shared client, so the CSRF
// token and every refusal come from the same place). Refusals are the server's own words. The
// busy and hours checks run first from what the server page already knew, like the form's `busy`.
//
// The link arrives after the `#` (app/share-target/route.ts), is moved to this tab's sessionStorage
// at once and removed from the address bar, so a reload or the trip through /login and back keeps it
// without it sitting in the URL or the history. A signed-out visitor is sent to /login with it kept.
type Phase =
  | { name: "reading" }
  | { name: "nothing" }
  | { name: "dismissed" }
  | { name: "signing_in" }
  | { name: "ready"; link: SharedLink }
  | { name: "sent"; meeting: Meeting };

export function ShareFlow({
  signedIn,
  templates = [],
  busy = false,
  capMessage = null,
}: {
  signedIn: boolean;
  templates?: Template[];
  busy?: boolean;
  // Set when the account has used its recorded hours; the sentence the server gives for it.
  capMessage?: string | null;
}) {
  const [phase, setPhase] = useState<Phase>({ name: "reading" });
  const [templateId, setTemplateId] = useState(String(templates[0]?.id ?? ""));
  const [error, setError] = useState<string | null>(null);
  const [locked, setLocked] = useState(false);
  const [pending, setPending] = useState(false);

  // Kept in a ref across React's development double-run of effects: the first run clears the
  // address bar, the second would read nothing.
  const found = useRef<SharedLink | null | undefined>(undefined);
  useEffect(() => {
    if (found.current === undefined) {
      const { link: fromAddress } = readShareFragment(window.location.hash);
      if (window.location.hash) {
        // Out of the address bar and the history entry, whatever it held.
        window.history.replaceState(null, "", window.location.pathname);
      }
      found.current = fromAddress ?? peekShare();
      if (found.current) stashShare(found.current);
    }
    const link = found.current;
    if (link && !signedIn) window.location.replace("/login");
    setPhase(!link ? { name: "nothing" } : !signedIn ? { name: "signing_in" } : { name: "ready", link });
  }, [signedIn]);

  async function send(link: SharedLink) {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      const meeting = await api.meetings.create({ url: link.url, template_id: Number(templateId) });
      clearShare();
      setPhase({ name: "sent", meeting });
    } catch (caught) {
      setError(errorMessage(caught));
      if (caught instanceof ApiError && caught.code === "hours_cap_reached") setLocked(true);
      setPending(false);
    }
  }

  if (phase.name === "reading" || phase.name === "signing_in") {
    return (
      <Card title="Shared meeting link">
        <p role="status">
          {phase.name === "signing_in" ? "Taking you to sign in. Your link will be here when you are back." : "Reading what you shared..."}
        </p>
        {phase.name === "signing_in" ? (
          <p className="note">
            <AppLink href="/login">Go to sign in</AppLink>
          </p>
        ) : null}
      </Card>
    );
  }

  if (phase.name === "nothing") {
    return (
      <Card title="Shared meeting link">
        <div className="stack-sm">
          <Alert kind="notice" role="alert">
            We could not find a Google Meet, Microsoft Teams or Zoom meeting link in what you shared. Open the meeting in its app, use
            Share on the invitation or the link itself, and choose Beyric Notes again. You can also paste the link on
            the Meetings page.
          </Alert>
          <div>
            <Button href="/">Go to Meetings</Button>
          </div>
        </div>
      </Card>
    );
  }

  if (phase.name === "dismissed") {
    return (
      <Card title="Shared meeting link">
        <div className="stack-sm">
          <Alert kind="info" role="status">
            Nothing was sent. The link was not kept.
          </Alert>
          <div>
            <Button href="/">Go to Meetings</Button>
          </div>
        </div>
      </Card>
    );
  }

  if (phase.name === "sent") {
    return (
      <Card title="Note taker sent">
        <div className="stack-sm">
          <Alert kind="info" role="status">
            The note taker is on its way to {phase.meeting.title}. If the host has a waiting room, let it in when it
            asks to join.
          </Alert>
          <div>
            <Button href={`/meetings/${phase.meeting.id}`}>Open the meeting</Button>
          </div>
        </div>
      </Card>
    );
  }

  const { link } = phase;
  const blocked = busy ? "A bot is already on a meeting. Wait for it to finish first, then share the link again." : capMessage;
  return (
    <Card title="Send the note taker to this meeting?">
      <div className="stack-sm">
        <p>
          <strong>{describeLink(link)}</strong>
        </p>
        {link.others > 0 ? (
          <Alert kind="info">
            What you shared held {link.others === 1 ? "another meeting link" : `${link.others} more meeting links`}.
            This is the first one.
          </Alert>
        ) : null}
        {blocked ? <Alert kind="notice" role="alert">{blocked}</Alert> : null}
        {error ? <Alert kind="error">{error}</Alert> : null}
        <SelectField
          id="template_id"
          label="Notes template"
          options={templates.map((t) => [t.id, t.name] as const)}
          value={templateId}
          onChange={(event) => setTemplateId(event.target.value)}
        />
        <div className="row-wrap">
          <Button disabled={pending || locked || blocked !== null} onClick={() => send(link)}>
            {pending ? "Sending..." : "Send note taker"}
          </Button>
          <Button
            kind="quiet"
            type="button"
            onClick={() => {
              clearShare();
              setPhase({ name: "dismissed" });
            }}
          >
            Not now
          </Button>
        </div>
        <p className="note send-note">
          <Icon name="info" size={15} />
          Tell participants that this meeting will be recorded.
        </p>
      </div>
    </Card>
  );
}
