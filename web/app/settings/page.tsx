import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { SettingsView } from "@/components/app/settings-view";
import { getReviewOpen, getUnreadNotifications, load, requireOwner } from "@/lib/api/server";

export const metadata: Metadata = { title: "Settings", robots: { index: false, follow: false } };

export default async function SettingsPage() {
  const api = await requireOwner();
  const email = await load(api.settings.email.get());
  return (
    <AppShell current="settings" reviewOpen={await getReviewOpen()} unread={await getUnreadNotifications()}>
      <SettingsView email={email} />
    </AppShell>
  );
}
