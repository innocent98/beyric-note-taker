// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LoginForm } from "@/components/app/login-form";
import { ShareFlow } from "@/components/share/share-flow";
import { ApiError } from "@/lib/api/client";
import type { Meeting, Template } from "@/lib/api/types";
import { findMeetingLink } from "@/lib/share";
import { PENDING_MINUTES, clearShare, hasPendingShare, peekShare, readShareFragment, stashShare } from "@/lib/share-pending";
import { captured } from "./helpers";

const mocks = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), login: vi.fn(), create: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push, replace: mocks.replace, refresh: mocks.refresh }),
}));
vi.mock("@/lib/api/browser", () => ({ api: { login: mocks.login, meetings: { create: mocks.create } } }));

const templates = captured<{ templates: Template[] }>("11-templates-seeded.json").templates;
const MEET = "https://meet.google.com/abc-defg-hij";
const FRAGMENT = `#link=${encodeURIComponent(MEET)}&others=0`;

// jsdom cannot navigate, so location is replaced by a view of the real one with a spy for replace().
const replaceSpy = vi.fn();
function arriveAt(hash: string) {
  window.history.replaceState(null, "", `/share${hash}`);
  const real = window.location;
  vi.stubGlobal("location", {
    get hash() {
      return real.hash;
    },
    get pathname() {
      return real.pathname;
    },
    replace: replaceSpy,
  });
}

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
  replaceSpy.mockReset();
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("share page, signed in", () => {
  it("shows the link found in what was shared, hides the address bar fragment, and waits for a tap", async () => {
    arriveAt(FRAGMENT);
    render(<ShareFlow signedIn templates={templates} />);
    expect(await screen.findByText("Google Meet abc-defg-hij")).toBeTruthy();
    expect(window.location.hash).toBe("");
    expect(window.history.length).toBeGreaterThan(0);
    expect(mocks.create).not.toHaveBeenCalled();
    expect(screen.getByText("Tell participants that this meeting will be recorded.")).toBeTruthy();
  });

  it("sends the link with the chosen template, as the Meetings form does, and says so in plain words", async () => {
    arriveAt(FRAGMENT);
    mocks.create.mockResolvedValue(captured<Meeting>("33-meeting-create.json"));
    render(<ShareFlow signedIn templates={templates} />);
    await screen.findByText("Google Meet abc-defg-hij");
    await userEvent.selectOptions(screen.getByLabelText("Notes template"), "2");
    await userEvent.click(screen.getByRole("button", { name: "Send note taker" }));
    expect(mocks.create).toHaveBeenCalledWith({ url: MEET, template_id: 2 });
    expect(await screen.findByText(/The note taker is on its way to Google Meet abc-defg-hij/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open the meeting" }).getAttribute("href")).toBe("/meetings/1");
    // The link is not kept once it is sent.
    expect(hasPendingShare()).toBe(false);
  });

  it("keeps the link after a reload (the address is clean by then)", async () => {
    arriveAt(FRAGMENT);
    const first = render(<ShareFlow signedIn templates={templates} />);
    await screen.findByText("Google Meet abc-defg-hij");
    first.unmount();
    arriveAt("");
    render(<ShareFlow signedIn templates={templates} />);
    expect(await screen.findByText("Google Meet abc-defg-hij")).toBeTruthy();
  });

  it.each([
    [
      "an unsupported link",
      new ApiError(400, "invalid_meeting_url", "That is not a Google Meet or Zoom link. Paste the full meeting link."),
      /That is not a Google Meet or Zoom link/,
    ],
    [
      "a bot already on a meeting",
      new ApiError(409, "bot_already_active", "A bot is already on a meeting. Wait for it to finish first."),
      /A bot is already on a meeting\. Wait for it to finish first\./,
    ],
    [
      "the note taker busy with someone else's meeting",
      new ApiError(409, "bot_busy", "The note taker is busy right now. Try again in a few minutes."),
      /The note taker is busy right now/,
    ],
    [
      "the hours cap",
      new ApiError(403, "hours_cap_reached", "You have used your 5 recorded hours for the pilot, so the note taker will not join new meetings. Write to us at hello@beyrictech.com if you need more."),
      /You have used your 5 recorded hours/,
    ],
    [
      "a bot that could not be sent",
      new ApiError(502, "bot_send_failed", "The bot could not be sent. Please try again in a moment."),
      /The bot could not be sent/,
    ],
  ])("shows the server's own words for %s and keeps the link", async (_name, refusal, words) => {
    arriveAt(FRAGMENT);
    mocks.create.mockRejectedValue(refusal);
    render(<ShareFlow signedIn templates={templates} />);
    await screen.findByText("Google Meet abc-defg-hij");
    await userEvent.click(screen.getByRole("button", { name: "Send note taker" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(words);
    expect(screen.getByText("Google Meet abc-defg-hij")).toBeTruthy();
    expect(hasPendingShare()).toBe(true);
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it("stops offering the send after the hours cap refusal", async () => {
    arriveAt(FRAGMENT);
    mocks.create.mockRejectedValue(new ApiError(403, "hours_cap_reached", "You have used your 5 recorded hours."));
    render(<ShareFlow signedIn templates={templates} />);
    await screen.findByText("Google Meet abc-defg-hij");
    await userEvent.click(screen.getByRole("button", { name: "Send note taker" }));
    await screen.findByRole("alert");
    expect((screen.getByRole("button", { name: "Send note taker" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("is locked up front, in the server's sentence, while a bot is on a meeting", async () => {
    arriveAt(FRAGMENT);
    render(<ShareFlow signedIn templates={templates} busy />);
    await screen.findByText("Google Meet abc-defg-hij");
    expect(screen.getByRole("alert").textContent).toMatch(/A bot is already on a meeting\. Wait for it to finish first/);
    expect((screen.getByRole("button", { name: "Send note taker" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("is locked up front when the recorded hours are used", async () => {
    arriveAt(FRAGMENT);
    render(<ShareFlow signedIn templates={templates} capMessage="You have used your 5 recorded hours for the pilot." />);
    await screen.findByText("Google Meet abc-defg-hij");
    expect(screen.getByRole("alert").textContent).toMatch(/used your 5 recorded hours/);
    expect((screen.getByRole("button", { name: "Send note taker" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("says when what was shared held more than one link", async () => {
    arriveAt(`#link=${encodeURIComponent(MEET)}&others=2`);
    render(<ShareFlow signedIn templates={templates} />);
    expect(await screen.findByText(/2 more meeting links/)).toBeTruthy();
  });

  it("says there was no meeting link, and offers the Meetings page", async () => {
    arriveAt("#none");
    render(<ShareFlow signedIn templates={templates} />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/could not find a Google Meet, Microsoft Teams or Zoom meeting link/);
    expect(screen.getByRole("link", { name: "Go to Meetings" }).getAttribute("href")).toBe("/meetings");
    expect(mocks.create).not.toHaveBeenCalled();
  });

  it("says there was nothing when opened with no share at all", async () => {
    arriveAt("");
    render(<ShareFlow signedIn templates={templates} />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/could not find/);
  });

  it("drops the link on Not now", async () => {
    arriveAt(FRAGMENT);
    render(<ShareFlow signedIn templates={templates} />);
    await screen.findByText("Google Meet abc-defg-hij");
    await userEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(await screen.findByText("Nothing was sent. The link was not kept.")).toBeTruthy();
    expect(hasPendingShare()).toBe(false);
    expect(mocks.create).not.toHaveBeenCalled();
  });

  it("never shows a Zoom passcode", async () => {
    arriveAt(`#link=${encodeURIComponent("https://us02web.zoom.us/j/81234567890?pwd=topsecret")}&others=0`);
    const { container } = render(<ShareFlow signedIn templates={templates} />);
    expect(await screen.findByText("Zoom meeting 81234567890 (with passcode)")).toBeTruthy();
    expect(container.textContent).not.toContain("topsecret");
  });

  it("ignores a fragment that is not a meeting link", async () => {
    arriveAt(`#link=${encodeURIComponent("https://evil.example/abc-defg-hij")}&others=0`);
    render(<ShareFlow signedIn templates={templates} />);
    expect((await screen.findByRole("alert")).textContent).toMatch(/could not find/);
  });
});

describe("share page, signed out", () => {
  it("keeps the link in this tab and goes to /login", async () => {
    arriveAt(FRAGMENT);
    render(<ShareFlow signedIn={false} />);
    expect(await screen.findByText(/Taking you to sign in/)).toBeTruthy();
    expect(replaceSpy).toHaveBeenCalledWith("/login");
    expect(window.location.hash).toBe("");
    expect(peekShare()?.url).toBe(MEET);
    expect(mocks.create).not.toHaveBeenCalled();
  });

  it("does not go to /login when nothing usable was shared", async () => {
    arriveAt("#none");
    render(<ShareFlow signedIn={false} />);
    await screen.findByRole("alert");
    expect(replaceSpy).not.toHaveBeenCalled();
  });
});

describe("login round trip", () => {
  async function signIn() {
    mocks.login.mockResolvedValue({ authenticated: true, csrf_token: "x", user: null, usage: null });
    render(<LoginForm />);
    await userEvent.type(screen.getByLabelText("Work email"), "owner@example.test");
    await userEvent.type(screen.getByLabelText("Password"), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  }

  it("returns to /share after sign-in when a shared link is waiting", async () => {
    stashShare(findMeetingLink({ text: MEET })!);
    await signIn();
    expect(mocks.replace).toHaveBeenCalledWith("/share");
  });

  it("goes to the Meetings page when nothing is waiting", async () => {
    await signIn();
    expect(mocks.replace).toHaveBeenCalledWith("/meetings");
  });
});

describe("pending share storage", () => {
  const link = findMeetingLink({ text: MEET })!;

  it("expires after the wait is over, and forgets it", () => {
    stashShare(link);
    expect(peekShare(Date.now() + (PENDING_MINUTES - 1) * 60_000)?.url).toBe(MEET);
    expect(peekShare(Date.now() + (PENDING_MINUTES + 1) * 60_000)).toBeNull();
    expect(hasPendingShare()).toBe(false);
  });

  it("checks what it reads: planted or broken values are dropped", () => {
    for (const value of [
      "not json",
      "null",
      JSON.stringify({ url: "https://evil.example/abc-defg-hij", at: Date.now() }),
      JSON.stringify({ url: MEET }),
      JSON.stringify({ url: 5, at: Date.now() }),
    ]) {
      window.sessionStorage.setItem("beyric.pending-share", value);
      expect(peekShare()).toBeNull();
    }
  });

  it("does nothing, without throwing, when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => stashShare(link)).not.toThrow();
    expect(peekShare()).toBeNull();
    vi.restoreAllMocks();
    clearShare();
  });

  it("reads the fragment the route sends, and refuses anything else in it", () => {
    expect(readShareFragment(FRAGMENT).link?.url).toBe(MEET);
    expect(readShareFragment("#none")).toEqual({ link: null, seen: true });
    expect(readShareFragment("").link).toBeNull();
    expect(readShareFragment("#link=javascript%3Aalert(1)").link).toBeNull();
    expect(readShareFragment(`#link=${encodeURIComponent(MEET)}&others=-5`).link?.others).toBe(0);
    expect(readShareFragment(`#link=${encodeURIComponent(MEET)}&others=9999`).link?.others).toBe(99);
  });
});
