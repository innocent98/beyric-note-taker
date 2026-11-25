// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingView } from "@/components/app/meeting-view";
import type { Meeting, TeamList, Template } from "@/lib/api/types";
import { teamsBody, workspacesBody } from "./helpers";

// A meeting the person's saved auto-share rule published says so on its page. Every meeting body is a real
// response (docs/fe-captures/workspaces/auto-share-NN-*.json, `scripts/capture_auto_share_label.py`): the
// recorder after the rule ran (11), a teammate reading the same meeting (12), after Stop sharing (14), shared
// again by hand (15) and born shared by its workspace (17). The teams are Pat's (teams capture 46: Platform
// team is 4, the team the capture shared with).

const mocks = vi.hoisted(() => ({ unshare: vi.fn(), push: vi.fn(), refresh: vi.fn(), milestones: vi.fn(), seriesList: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push, refresh: mocks.refresh }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    milestones: { forMeeting: mocks.milestones, confirm: vi.fn() },
    series: { list: mocks.seriesList, create: vi.fn() },
    meetings: { unshare: mocks.unshare, share: vi.fn(), move: vi.fn(), moveBack: vi.fn() },
  },
}));

const templates: Template[] = [{ id: 1, name: "General", instructions: "g" }];
const teams = teamsBody<TeamList>("46-teams-list.json").teams;
const detail = (file: string) => workspacesBody<Meeting>(file);
const show = (m: Meeting) => render(<MeetingView initial={m} templates={templates} teams={teams} />);
const LABEL = /by your auto-share rule/;

beforeEach(() => {
  cleanup();
  Object.values(mocks).forEach((fn) => fn.mockReset());
  mocks.milestones.mockResolvedValue({ milestones: [] });
  mocks.seriesList.mockResolvedValue({ timezone: "UTC", series: [] });
});

describe("a meeting the auto-share rule shared", () => {
  it("says which team and why, with a way to stop and a way to change the rule", () => {
    const m = detail("auto-share-11-meeting-personal-after-rule-recorder.json");
    expect(m.shared_by_auto_share).toBe(true);
    show(m);
    expect(screen.getByRole("heading", { name: "Shared with Platform team" })).toBeTruthy();
    expect(document.body.textContent).toContain("Shared with Platform team by your auto-share rule.");
    expect(screen.getByRole("button", { name: "Stop sharing" })).toBeTruthy(); // the existing unshare
    expect(screen.getByRole("link", { name: "Settings" }).getAttribute("href")).toBe("/settings");
  });
});

describe("the label is only drawn when it is true", () => {
  it.each([
    ["a teammate reading it", "auto-share-12-meeting-personal-after-rule-teammate.json"],
    ["a meeting shared again by hand", "auto-share-15-meeting-share-by-hand.json"],
    ["a meeting born shared by its workspace", "auto-share-17-meeting-born-shared-recorder.json"],
  ])("%s", (_name, file) => {
    show(detail(file));
    expect(document.body.textContent).not.toMatch(LABEL);
  });

  it("not once the meeting is private again", () => {
    const m = detail("auto-share-14-meeting-unshare.json");
    expect(m.shared_with_team).toBe(false);
    show(m);
    expect(document.body.textContent).not.toMatch(LABEL);
  });

  it("not for an older body without the field", () => {
    const m = { ...detail("auto-share-11-meeting-personal-after-rule-recorder.json") } as Partial<Meeting>;
    delete m.shared_by_auto_share;
    show(m as Meeting);
    expect(document.body.textContent).not.toMatch(LABEL);
  });
});
