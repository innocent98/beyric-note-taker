import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { ShareFlow } from "@/components/share/share-flow";
import { PageHead } from "@/components/ui";
import { getSession, load, serverApi } from "@/lib/api/server";

export const metadata: Metadata = { title: "Share a meeting link", robots: { index: false, follow: false } };

// Where the share sheet lands (app/share-target/route.ts sends the browser here with the link after
// the `#`, which only the browser sees). Signed out: the client component keeps the link in the tab
// and goes to /login. Signed in: the same checks the Meetings page makes are read here (a bot
// already on a meeting, the recorded-hours cap) and the owner confirms with one tap.
export default async function SharePage() {
  const session = await getSession();
  if (!session?.authenticated) return <ShareFlow signedIn={false} />;
  const api = await serverApi();
  const [list, templates] = await load(Promise.all([api.meetings.list(), api.templates.list()]));
  const busy = list.meetings.some((m) => m.status === "bot_sent" || m.status === "processing");
  const usage = session.usage;
  const capped = usage?.cap_seconds != null && usage.used_seconds >= usage.cap_seconds;
  const capMessage = capped
    ? `You have used your ${(usage.cap_seconds ?? 0) / 3600} recorded hours for the pilot, so the note taker will not join new meetings. Write to us at hello@beyrictech.com if you need more.`
    : null;
  return (
    <AppShell current="meetings">
      <div className="stack">
        <PageHead title="Shared meeting link" subtitle="You shared a link with Beyric Notes from another app." />
        <ShareFlow signedIn templates={templates} busy={busy} capMessage={capMessage} />
      </div>
    </AppShell>
  );
}
