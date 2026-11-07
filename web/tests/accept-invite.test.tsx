// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AcceptInvite } from "@/components/auth/accept-invite";
import { ApiError } from "@/lib/api/client";
import type { InvitePreview, Team } from "@/lib/api/types";
import { teamBody } from "./helpers";

// The preview bodies are the captures 15 and 16 (docs/fe-captures/team-w3/).
const mocks = vi.hoisted(() => ({ preview: vi.fn(), accept: vi.fn(), session: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/lib/api/browser", () => ({ api: { team: { preview: mocks.preview, accept: mocks.accept }, session: mocks.session } }));

const brandNew = teamBody<InvitePreview>("15-preview-new-person.json");
const existing = teamBody<InvitePreview>("16-preview-existing-account.json");
const team = teamBody<Team>("27-accept.json");
const signedIn = (email: string) => ({ authenticated: true, user: { email } });
const visitor = { authenticated: false, user: null };

function open(token = "tok") {
  window.history.replaceState(null, "", `/accept-invite?token=${token}`);
  return render(<AcceptInvite />);
}

beforeEach(() => Object.values(mocks).forEach((fn) => fn.mockReset()));
afterEach(cleanup);

describe("the invitation link page (the designer's Join your team)", () => {
  it("takes the token out of the address and says who invited you", async () => {
    mocks.preview.mockResolvedValue(existing);
    mocks.session.mockResolvedValue(visitor);
    open("abc123");
    await screen.findByRole("heading", { name: "Join your team" });
    expect(screen.getByText(existing.inviter_name!).closest(".invite-banner-text")?.textContent).toBe(
      `${existing.inviter_name} invited you to the ${existing.team_name} workspace`,
    );
    expect(window.location.search).toBe("");
    expect(mocks.preview).toHaveBeenCalledWith("abc123");
  });

  it("sends an address with no account to sign up with the link", async () => {
    mocks.preview.mockResolvedValue(brandNew);
    mocks.session.mockResolvedValue(visitor);
    open("abc123");
    const link = await screen.findByRole("link", { name: "Create your account" });
    expect(link.getAttribute("href")).toBe("/signup?invite=abc123");
    expect(screen.getByRole("heading", { name: "Join your team" })).toBeTruthy();
    // The invited address, locked (a lock beside it, not an input).
    expect(screen.getByRole("textbox", { name: "Email" }).textContent).toBe(brandNew.email);
  });

  it("asks a visitor with an account to sign in first", async () => {
    mocks.preview.mockResolvedValue(existing);
    mocks.session.mockResolvedValue(visitor);
    open();
    expect(await screen.findByRole("link", { name: "Go to sign in" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Join Beyric" })).toBeNull();
  });

  it("tells someone signed in as another address which address the invitation is for", async () => {
    mocks.preview.mockResolvedValue(existing);
    mocks.session.mockResolvedValue(signedIn("someone@else.test"));
    open();
    expect(await screen.findByRole("heading", { name: "This invitation is for another address" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy();
  });

  it("warns that the person's own team is replaced, then accepts and goes to Team", async () => {
    mocks.preview.mockResolvedValue(existing);
    mocks.session.mockResolvedValue(signedIn(existing.email));
    mocks.accept.mockResolvedValue(team);
    open("abc123");
    expect(await screen.findByText(/Your own team will be replaced by this one/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Join Beyric" }));
    expect(mocks.accept).toHaveBeenCalledWith("abc123");
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/team"));
  });

  it("shows already_on_team as the API wrote it and stays", async () => {
    mocks.preview.mockResolvedValue(existing);
    mocks.session.mockResolvedValue(signedIn(existing.email));
    mocks.accept.mockRejectedValue(new ApiError(409, "already_on_team", "You are on another team. Leave it first, then accept this invitation."));
    open();
    await userEvent.click(await screen.findByRole("button", { name: "Join Beyric" }));
    expect(await screen.findByText("You are on another team. Leave it first, then accept this invitation.")).toBeTruthy();
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it("gives one answer for a dead link, and for a missing one", async () => {
    mocks.preview.mockRejectedValue(new ApiError(400, "invite_invalid", "That invitation is not valid any more. Ask for a new one."));
    mocks.session.mockResolvedValue(visitor);
    open("dead");
    expect(await screen.findByRole("heading", { name: "This invitation does not work" })).toBeTruthy();
    cleanup();
    window.history.replaceState(null, "", "/accept-invite");
    render(<AcceptInvite />);
    expect(await screen.findByRole("heading", { name: "This invitation does not work" })).toBeTruthy();
  });
});
