import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { EmailSettingsSection } from "@/components/app/email-settings-section";
import { PageHead } from "@/components/ui";
import { getReviewOpen, getUnreadNotifications, load, requireOwner } from "@/lib/api/server";

export const metadata: Metadata = { title: "Settings", robots: { index: false, follow: false } };

// Thin on purpose: each section of Settings is its own component, so sections added by other work
// (Calendar) slot in beside this one.
export default async function SettingsPage() {
  const api = await requireOwner();
  const email = await load(api.settings.email.get());
  return (
    <AppShell current="settings" reviewOpen={await getReviewOpen()} unread={await getUnreadNotifications()}>
      <div className="stack-md measure">
        <PageHead title="Settings" subtitle="Preferences for your notes and transcripts." />
        <EmailSettingsSection email={email} />
      </div>
    </AppShell>
  );
}
