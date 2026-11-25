import { describe, expect, it, vi } from "vitest";
import { createApi, isTeamScoped, TEAM_HEADER } from "@/lib/api/client";

// The interceptor: `X-Team-Id` goes on the routes that read it (docs/fe-integration-guide-teams.md section
// 10) and on no others, from the tab's team, from a server page's team, or from one call's own team.

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function setup(options: Partial<Parameters<typeof createApi>[0]> = {}, respond: () => Response = () => json(200, {})) {
  const calls: { url: string; init: RequestInit }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    calls.push({ url: String(input), init });
    return respond();
  });
  const api = createApi({ fetch: fetchMock as unknown as typeof fetch, ...options });
  return { api, calls };
}
const sent = (call: { init: RequestInit }) => (call.init.headers as Record<string, string>)[TEAM_HEADER];

describe("which calls carry the team", () => {
  it.each([
    "/api/team",
    "/api/team/members",
    "/api/team/members/7",
    "/api/team/invites",
    "/api/team/invites/3/resend",
    "/api/team/invites/3",
    "/api/tasks",
    "/api/tasks?limit=500",
    "/api/projects",
    "/api/projects/4",
    "/api/milestones",
    "/api/milestones/2/confirm",
    "/api/sprints",
    "/api/sprints/9/close",
    "/api/series",
    "/api/integrations/slack",
    "/api/integrations/linear/teams",
    "/api/meetings/12/milestones",
    "/api/series/removed",
    // The reads that show one team at a time (owner's request 2026-11-14).
    "/api/meetings",
    "/api/meetings?limit=10&from=2026-11-01",
    "/api/decisions",
    "/api/decisions?status=approved",
    "/api/review?state=checked",
    "/api/session",
    // The meeting picker's preview is a POST that asks about the active team.
    "/api/recipes/picker/resolve",
  ])("%s is team-scoped", (path) => {
    expect(isTeamScoped(path)).toBe(true);
  });

  it.each([
    ["POST", "/api/session"],
    ["DELETE", "/api/session"],
  ])("%s %s is not: logging in and out are the person's own", (method, path) => {
    expect(isTeamScoped(path, method)).toBe(false);
  });

  // Workspaces design, section 7: everything else names its workspace, even the routes that only open one
  // thing by its id (the server ignores the header there) and the ones the later slices will scope.
  it.each([
    ["POST", "/api/meetings"],
    ["POST", "/api/ask"],
    ["GET", "/api/ask/scope"],
    ["GET", "/api/notifications"],
    ["POST", "/api/notifications/read-all"],
    ["GET", "/api/templates"],
    ["POST", "/api/templates"],
    ["GET", "/api/vocabulary"],
    ["GET", "/api/calendar"],
    ["PUT", "/api/calendar/settings"],
    ["POST", "/api/calendar/google/connect"],
    ["GET", "/api/not-recorded"],
    ["GET", "/api/settings/tasks"],
    ["GET", "/api/settings/auto-share"],
    ["GET", "/api/settings/reminders"],
    ["GET", "/api/settings/bot-name"],
    ["GET", "/api/tasks/5"],
    ["GET", "/api/series/3"],
    ["GET", "/api/decisions/4"],
    ["POST", "/api/review/4/check"],
    ["GET", "/api/meetings/12"],
    ["POST", "/api/meetings/12/share"],
  ])("%s %s is workspace-scoped", (method, path) => {
    expect(isTeamScoped(path, method)).toBe(true);
  });

  it("names the workspace on every recipe, brief and draft call (the server ignores it where the call is about one draft)", () => {
    expect(isTeamScoped("/api/documents", "POST")).toBe(true);
    expect(isTeamScoped("/api/documents", "GET")).toBe(true);
    expect(isTeamScoped("/api/documents/5", "GET")).toBe(true);
    expect(isTeamScoped("/api/documents/5/share", "POST")).toBe(true);
    expect(isTeamScoped("/api/recipes/picker/resolve", "POST")).toBe(true);
    expect(isTeamScoped("/api/recipes", "GET")).toBe(true);
    expect(isTeamScoped("/api/briefs", "GET")).toBe(true);
  });

  it.each([
    "/api/teams",
    "/api/team/invites/preview",
    "/api/team/invites/accept",
    "/api/settings/name",
    "/api/settings/email",
    "/api/settings/language",
    "/api/settings/time-zone",
    "/api/settings/recording-ack",
    "/api/billing/plans",
    "/api/billing/subscription",
    "/api/billing/invoices/BN-2026-000001/pdf",
    "/api/admin/users",
    "/api/pilot",
    "/api/waitlist",
    "/api/signup",
    "/api/verify-email",
    "/api/password/forgot",
    "/api/mail/opt-out",
    "/api/auth/google/start",
    "/api/calendar/google/callback",
    "/api/calendar/microsoft/callback",
    "/healthz",
  ])("%s is the person's own, no workspace", (path) => {
    expect(isTeamScoped(path)).toBe(false);
  });
});

describe("the header", () => {
  it("is sent from the tab's team on a team call and not on another", async () => {
    const { api, calls } = setup({ teamId: () => 5 });
    await api.team.get();
    await api.teams.list();
    await api.meetings.get(12).catch(() => undefined);
    expect(calls.map(sent)).toEqual(["5", undefined, "5"]);
  });

  it("is not sent when there is no team (the default team, and the switch off)", async () => {
    for (const teamId of [() => null, () => undefined, undefined]) {
      const { api, calls } = setup({ teamId });
      await api.team.get();
      expect(sent(calls[0])).toBeUndefined();
    }
  });

  it("is read on every call, so a change of team is the next call's", async () => {
    let team: number | null = 5;
    const { api, calls } = setup({ teamId: () => team });
    await api.tasks.list();
    team = 9;
    await api.tasks.list();
    team = null;
    await api.tasks.list();
    expect(calls.map(sent)).toEqual(["5", "9", undefined]);
  });

  it("is on a write too, next to the CSRF token", async () => {
    const { api, calls } = setup({ teamId: () => 5 }, () => json(200, { csrf_token: "tok", authenticated: true }));
    await api.projects.remove(3).catch(() => undefined);
    const write = calls.find((c) => c.init.method === "DELETE");
    expect(write && sent(write)).toBe("5");
    expect((write!.init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok");
  });

  it("a call's own team wins over the tab's, and null means none", async () => {
    const { api, calls } = setup({ teamId: () => 5 });
    await api.series.list(8);
    await api.series.list(null);
    await api.team.members(8);
    await api.milestones.forMeeting(12, 8);
    expect(calls.map(sent)).toEqual(["8", undefined, "8", "8"]);
  });

  it("a share names its team in the body; the header is the tab's workspace", async () => {
    const { api, calls } = setup({ teamId: () => 5 }, () => json(200, { csrf_token: "tok", authenticated: true }));
    await api.meetings.share(1, 4);
    const post = calls.find((c) => c.init.method === "POST")!;
    expect(sent(post)).toBe("5");
    // The team of a share is in the body, not the header.
    expect(JSON.parse(String(post.init.body))).toEqual({ team_id: 4 });
  });
});

describe("a team that is gone", () => {
  const teamGone = () => json(404, { error: { code: "not_found", message: "No such team." } });

  it("calls onTeamGone for 404 'No such team.' on a call that named a team, and still throws", async () => {
    const onTeamGone = vi.fn();
    const { api } = setup({ teamId: () => 5, onTeamGone }, teamGone);
    await expect(api.team.get()).rejects.toMatchObject({ status: 404, code: "not_found", message: "No such team." });
    expect(onTeamGone).toHaveBeenCalledTimes(1);
  });

  it("does nothing for a team a call named for itself (a meeting's): reloading cannot change it", async () => {
    const onTeamGone = vi.fn();
    const { api } = setup({ teamId: () => 5, onTeamGone }, teamGone);
    await expect(api.series.list(8)).rejects.toMatchObject({ status: 404 });
    await expect(api.milestones.forMeeting(3, 8)).rejects.toMatchObject({ status: 404 });
    await expect(api.tasks.list({ limit: 1 }, 8)).rejects.toMatchObject({ status: 404 });
    expect(onTeamGone).not.toHaveBeenCalled();
  });

  it("does nothing when the call named no team", async () => {
    const onTeamGone = vi.fn();
    const { api } = setup({ onTeamGone }, teamGone);
    await expect(api.team.get()).rejects.toMatchObject({ status: 404 });
    expect(onTeamGone).not.toHaveBeenCalled();
  });

  it("does nothing for another 404 (no such task, no such meeting)", async () => {
    const onTeamGone = vi.fn();
    const { api } = setup({ teamId: () => 5, onTeamGone }, () => json(404, { error: { code: "not_found", message: "No such meeting" } }));
    await expect(api.meetings.get(1)).rejects.toMatchObject({ status: 404 });
    await expect(api.milestones.forMeeting(1)).rejects.toMatchObject({ status: 404 });
    expect(onTeamGone).not.toHaveBeenCalled();
  });
});

describe("login and logout", () => {
  it("tell the browser to forget the remembered team", async () => {
    const onSessionChange = vi.fn();
    const { api } = setup({ onSessionChange }, () => json(200, { csrf_token: "tok", authenticated: true }));
    await api.login("a@example.test", "pw");
    expect(onSessionChange).toHaveBeenCalledTimes(1);
    await api.logout();
    expect(onSessionChange).toHaveBeenCalledTimes(2);
  });

  it("a meeting's series call names the workspace too (the server ignores it there)", () => {
    expect(isTeamScoped("/api/meetings/12/series")).toBe(true);
    expect(isTeamScoped("/api/meetings/12/recurring")).toBe(true);
  });
});
