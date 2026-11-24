import type { Metadata } from "next";
import { AppShell } from "@/components/app/shell";
import { RecipesView } from "@/components/app/recipes-view";
import { getReviewOpen, getTasksNew, getShellUser, getUnreadNotifications, load, requireTeamOwner, getShellTeams } from "@/lib/api/server";

export const metadata: Metadata = { title: "Recipes", robots: { index: false, follow: false } };

// The recipe library and the team brief of one team (`?team=`, else the default team).
export default async function RecipesPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const { api } = await requireTeamOwner("/recipes", await searchParams);
  const [recipes, brief] = await Promise.all([load(api.recipes.list()), load(api.brief.get())]);
  return (
    <AppShell
      current="recipes"
      reviewOpen={await getReviewOpen()}
      tasksNew={await getTasksNew()}
      unread={await getUnreadNotifications()}
      user={await getShellUser()}
      teams={await getShellTeams()}
    >
      <RecipesView initial={recipes} brief={brief} />
    </AppShell>
  );
}
