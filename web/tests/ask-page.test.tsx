import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import AskPage from "@/app/ask/page";
import { ApiError } from "@/lib/api/client";

// The server half of /ask: the scope comes from GET /api/ask/scope, a failed load leaves the box
// without chips, a lost login goes to /login.

const state = vi.hoisted(() => ({ fail: null as null | number, user: { name: "Adebayo Tosin", email: "a@b.test" } as { name: string | null; email: string } | null }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/ask",
  redirect: (to: string) => {
    throw new Error(`redirect:${to}`);
  },
  useRouter: () => ({ refresh: vi.fn() }),
}));
vi.mock("@/lib/api/server", () => ({
  requireOwner: async () => ({
    ask: {
      scope: async () => {
        if (state.fail !== null) throw new ApiError(state.fail, "x", "x");
        return { meetings: 3, decisions: 4, tasks: 5, suggestions: ["When is the launch?"] };
      },
    },
  }),
  getReviewOpen: async () => null,
  getTasksNew: async () => 0,
  getUnreadNotifications: async () => null,
  getShellUser: async () => state.user,
  getShellTeams: async () => null,
  getTimeZone: async () => null,
}));

describe("/ask page", () => {
  it("draws the scope line, the chips and the sidebar with Ask Beyric current", async () => {
    state.fail = null;
    const html = renderToStaticMarkup(await AskPage());
    expect(html).toContain("Searches 3 meetings, 4 decisions and 5 tasks");
    expect(html).toContain("When is the launch?");
    expect(html).toMatch(/href="\/ask"[^>]*aria-current="page"|aria-current="page"[^>]*href="\/ask"/);
  });

  it("still draws the box when the scope could not be loaded, without chips or a scope line", async () => {
    state.fail = 500;
    const html = renderToStaticMarkup(await AskPage());
    expect(html).toContain("Ask Beyric");
    expect(html).toContain("e.g. What did we decide about pricing?");
    expect(html).not.toContain("Searches");
    expect(html).not.toContain("ask-chip");
  });

  it("sends a lost login to /login", async () => {
    state.fail = 401;
    await expect(AskPage()).rejects.toThrow("redirect:/login");
  });
});
