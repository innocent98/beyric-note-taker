import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import DecisionsPage from "@/app/decisions/page";
import ReviewPage from "@/app/review/page";
import { AppShell } from "@/components/app/shell";
import { NEXT_PAGES, isNextPage } from "@/lib/routes";
import { workspaceBody } from "./helpers";
import type { DecisionList, ReviewList } from "@/lib/api/types";

// The sidebar's Decisions and Review links and the badge (`badges.review_open`), and the server half
// of the two pages.

const state = vi.hoisted(() => ({ calls: [] as string[], reviewOpen: 3 as number | null, authenticated: true }));
vi.mock("next/navigation", () => ({
  redirect: (to: string) => {
    throw new Error(`redirect:${to}`);
  },
  notFound: () => {
    throw new Error("not-found");
  },
  useRouter: () => ({ refresh: vi.fn() }),
}));
vi.mock("@/lib/api/server", () => ({
  requireOwner: async () => {
    if (!state.authenticated) throw new Error("redirect:/login");
    return {
      decisions: {
        list: async () => {
          state.calls.push("decisions");
          return workspaceBody<DecisionList>("13-decisions.json");
        },
      },
      review: {
        list: async (which: string) => {
          state.calls.push(`review:${which}`);
          return workspaceBody<ReviewList>(which === "open" ? "29-review-open.json" : "33-review-checked.json");
        },
      },
    };
  },
  getReviewOpen: async () => state.reviewOpen,
  load: <T,>(call: Promise<T>) => call,
}));

describe("sidebar", () => {
  it("links Decisions and Review and marks the current one", () => {
    const html = renderToStaticMarkup(<AppShell current="review">x</AppShell>);
    expect(html).toMatch(/href="\/decisions"/);
    expect(html).toMatch(/href="\/review"[^>]*aria-current="page"|aria-current="page"[^>]*href="\/review"/);
    expect(html).not.toMatch(/href="\/decisions"[^>]*aria-current/);
  });

  it("draws the open count beside Review, and says it to a screen reader", () => {
    const html = renderToStaticMarkup(<AppShell current="meetings" reviewOpen={3}>x</AppShell>);
    expect(html).toContain('<span class="nav-badge" aria-hidden="true">3</span>');
    expect(html).toContain(", 3 open");
  });

  it("draws no badge for 0 or an unknown count, and 99+ for a big one", () => {
    for (const none of [0, null, undefined]) {
      expect(renderToStaticMarkup(<AppShell current="meetings" reviewOpen={none}>x</AppShell>)).not.toContain("nav-badge");
    }
    expect(renderToStaticMarkup(<AppShell current="meetings" reviewOpen={150}>x</AppShell>)).toContain(">99+<");
  });
});

describe("routes", () => {
  it("are Next pages (client navigation, nonce CSP)", () => {
    expect(NEXT_PAGES).toEqual(expect.arrayContaining(["/decisions", "/review"]));
    expect(isNextPage("/decisions")).toBe(true);
    expect(isNextPage("/review")).toBe(true);
  });
});

describe("pages", () => {
  it("draws Decisions from GET /api/decisions, with the badge", async () => {
    state.calls = [];
    const html = renderToStaticMarkup(await DecisionsPage());
    expect(state.calls).toEqual(["decisions"]);
    expect(html).toContain("Google Meet nat-aaaa-bbb");
    expect(html).toContain(", 3 open");
  });

  it("draws Review from the open and the checked lists", async () => {
    state.calls = [];
    const html = renderToStaticMarkup(await ReviewPage());
    expect(state.calls.sort()).toEqual(["review:checked", "review:open"]);
    expect(html).toContain("Heard as");
    expect(html).toContain("Mark as checked");
  });

  it("send a visitor who is not logged in to /login", async () => {
    state.authenticated = false;
    await expect(DecisionsPage()).rejects.toThrow("redirect:/login");
    await expect(ReviewPage()).rejects.toThrow("redirect:/login");
    state.authenticated = true;
  });
});
