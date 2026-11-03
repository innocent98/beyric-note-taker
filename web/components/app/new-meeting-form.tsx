"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Alert, Button, Card, Field, Icon, SelectField } from "@/components/ui";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import type { Template } from "@/lib/api/types";

// "New meeting": POST /api/meetings, then on to the meeting's page. What the owner typed stays in
// the form after an error (the server never echoes the link back, and a Zoom link carries a
// password, so it is never put in a URL either). `busy` is true while a bot is already on a
// meeting: the server would answer 409, so the form says so instead.
export function NewMeetingForm({ templates, busy }: { templates: Template[]; busy: boolean }) {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [templateId, setTemplateId] = useState(String(templates[0]?.id ?? ""));
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      const meeting = await api.meetings.create({ url: url.trim(), template_id: Number(templateId) });
      router.push(`/meetings/${meeting.id}`);
    } catch (caught) {
      setError(errorMessage(caught));
      setPending(false);
      router.refresh();
    }
  }

  return (
    <Card title="New meeting">
      <form className="stack-sm" onSubmit={submit}>
        {error ? <Alert kind="error">{error}</Alert> : null}
        {busy && !error ? (
          <Alert kind="info">A bot is already on a meeting. Wait for it to finish first.</Alert>
        ) : null}
        <div className="row-wrap">
          <div className="field-grow">
            <Field
              id="url"
              label="Meeting link"
              icon="link"
              type="url"
              required
              inputMode="url"
              autoComplete="off"
              placeholder="Paste a Google Meet or Zoom link"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
            />
          </div>
          <div className="field-fixed">
            <SelectField
              id="template_id"
              label="Notes template"
              options={templates.map((t) => [t.id, t.name] as const)}
              value={templateId}
              onChange={(event) => setTemplateId(event.target.value)}
            />
          </div>
          <Button disabled={pending || busy}>{pending ? "Sending..." : "Send note taker"}</Button>
        </div>
        <p className="note send-note">
          <Icon name="info" size={15} />
          Tell participants that this meeting will be recorded.
        </p>
      </form>
    </Card>
  );
}
