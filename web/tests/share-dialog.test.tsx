// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TimeZoneProvider } from "@/components/app/time-zone-context";
import { MeetingView } from "@/components/app/meeting-view";
import { ShareDialog, emailOf, sentToast } from "@/components/app/share-dialog";
import { UnsubscribeForm } from "@/components/auth/unsubscribe-form";
import { ApiError } from "@/lib/api/client";
import type { Meeting, Share, ShareCreated, TeamMember, Template } from "@/lib/api/types";
import { captured, shareBody, shareCapture } from "./helpers";

// Fed the bodies captured from the running API (docs/fe-captures/share/, cited in
// docs/fe-integration-guide-share.md): the 202 answers, the list, the refusals.
const mocks = vi.hoisted(() => ({ create: vi.fn(), list: vi.fn(), optOut: vi.fn(), token: { value: "tok" as string | null } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    optOutOfMail: mocks.optOut,
    meetings: { shares: { create: mocks.create, list: mocks.list }, get: vi.fn() },
    milestones: { forMeeting: () => Promise.resolve({ milestones: [] }), confirm: vi.fn() },
  },
}));
vi.mock("@/components/auth/use-url-token", () => ({
  useUrlToken: () => ({ ready: true, token: mocks.token.value }),
}));

const member = (name: string, email: string, aliases: string[] = []): TeamMember => ({
  user_id: 1,
  name,
  email,
  role: "member",
  aliases,
  is_you: false,
  joined_at: "2026-11-01T10:00:00Z",
  open_tasks: 0,
  can_change_role: false,
  can_edit_aliases: false,
  can_remove: false,
});
const members = [member("Daniel Okoro", "daniel@example.test", ["Daniel", "Dan"]), member("Amara Nwosu", "amara@example.test")];
const created = shareBody<ShareCreated>("06-share-en-docx.json");
const waiting = shareBody<ShareCreated>("09-share-fr-waiting.json");
const listed = shareBody<{ shares: Share[] }>("08-shares-sent.json").shares;

beforeEach(() => {
  Object.values(mocks).forEach((fn) => typeof fn === "function" && fn.mockReset());
  mocks.token.value = "tok";
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) {
    this.removeAttribute("open");
  };
});
afterEach(cleanup);

function open(overrides: Partial<Parameters<typeof ShareDialog>[0]> = {}) {
  const onShared = vi.fn();
  const onClose = vi.fn();
  render(
    <ShareDialog
      open
      meetingId={7}
      title="Zoom 88205679674"
      participants={["Daniel", "Kemi Adeyemi"]}
      members={members}
      hasTranscript
      language="en"
      onClose={onClose}
      onShared={onShared}
      {...overrides}
    />,
  );
  return { onShared, onClose };
}

describe("the Share dialog", () => {
  it("draws the designer's fields in the designer's words", () => {
    open();
    expect(screen.getByRole("heading", { name: "Share notes" })).toBeTruthy();
    expect(screen.getByText("Send the notes from Zoom 88205679674 by email.")).toBeTruthy();
    for (const label of ["Meeting participants", "Add other people", "Language", "Attach a copy"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.getByLabelText("No attachment")).toBeTruthy();
    expect(screen.getByLabelText("PDF")).toBeTruthy();
    expect(screen.getByLabelText("Word")).toBeTruthy();
    expect(screen.getByText("Include the full transcript")).toBeTruthy();
    expect(screen.getByPlaceholderText("Add a short note for the recipients")).toBeTruthy();
    expect(screen.getByPlaceholderText("name@company.com, press Enter")).toBeTruthy();
    expect(screen.getByText("Notes are sent in the body of the email.")).toBeTruthy();
    expect(screen.getByText("Recipients get the notes as they were written.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send notes" })).toBeTruthy();
  });

  it("ticks a participant that matches a team member and shows their address", () => {
    open();
    expect((screen.getByLabelText("Send to Daniel") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText("daniel@example.test")).toBeTruthy();
    expect(screen.getByText("1 recipient")).toBeTruthy();
  });

  it("matches a name to a member by name or alias, case blind, and nothing else", () => {
    expect(emailOf("dan", members)).toBe("daniel@example.test");
    expect(emailOf("AMARA NWOSU", members)).toBe("amara@example.test");
    expect(emailOf("Kemi Adeyemi", members)).toBeNull();
    expect(emailOf("", members)).toBeNull();
  });

  it("offers 'Add email' beside a participant with no address, which ticks them", async () => {
    open();
    const box = screen.getByLabelText("Send to Kemi Adeyemi") as HTMLInputElement;
    expect(box.disabled).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Add email" }));
    await userEvent.type(screen.getByLabelText("Email address of Kemi Adeyemi"), "kemi@example.test{Enter}");
    expect(screen.getByText("kemi@example.test")).toBeTruthy();
    expect((screen.getByLabelText("Send to Kemi Adeyemi") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText("2 recipients")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add email" })).toBeNull();
  });

  it("does not take an address that is not one", async () => {
    open();
    await userEvent.click(screen.getByRole("button", { name: "Add email" }));
    await userEvent.type(screen.getByLabelText("Email address of Kemi Adeyemi"), "nope{Enter}");
    expect((screen.getByLabelText("Send to Kemi Adeyemi") as HTMLInputElement).checked).toBe(false);
    expect(screen.getByText("1 recipient")).toBeTruthy();
  });

  it("adds other people as chips (Enter or comma) and removes them", async () => {
    open();
    const input = screen.getByPlaceholderText("name@company.com, press Enter");
    await userEvent.type(input, "a@example.test,b@example.test{Enter}");
    expect(screen.getByText("a@example.test")).toBeTruthy();
    expect(screen.getByText("3 recipients")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Remove a@example.test" }));
    expect(screen.queryByText("a@example.test")).toBeNull();
  });

  it("says 'Choose at least one recipient' and disables Send with nobody picked", async () => {
    open({ participants: ["Kemi Adeyemi"] });
    expect(screen.getByText("Choose at least one recipient")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Send notes" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("refuses more than 10 people before asking the server", async () => {
    open({ participants: [] });
    const input = screen.getByPlaceholderText("name@company.com, press Enter");
    await userEvent.type(input, Array.from({ length: 11 }, (_, i) => `p${i}@example.test`).join(",") + "{Enter}");
    expect(screen.getByText("11 recipients: send to 10 at most")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Send notes" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("changes the language hint, and no longer promises a link to the original", async () => {
    open();
    await userEvent.selectOptions(screen.getByLabelText("Language"), "fr");
    expect(screen.getByText("Recipients get the notes translated, with the original below.")).toBeTruthy();
    expect(screen.queryByText(/link to the original/)).toBeNull();
  });

  it("changes the attachment hint", async () => {
    open();
    await userEvent.click(screen.getByLabelText("PDF"));
    expect(screen.getByText("Notes are sent in the email body with a PDF copy attached.")).toBeTruthy();
    await userEvent.click(screen.getByLabelText("Word"));
    expect(screen.getByText("Notes are sent in the email body with a Word copy attached.")).toBeTruthy();
  });

  it("hides the transcript tick for a meeting with no transcript", () => {
    open({ hasTranscript: false });
    expect(screen.queryByText("Include the full transcript")).toBeNull();
  });

  it("sends exactly what was chosen, then reports and closes", async () => {
    mocks.create.mockResolvedValue(created);
    const { onShared, onClose } = open({ language: "fr" });
    await userEvent.type(screen.getByPlaceholderText("name@company.com, press Enter"), "Client@Example.test{Enter}");
    await userEvent.click(screen.getByLabelText("PDF"));
    await userEvent.click(screen.getByText("Include the full transcript"));
    await userEvent.type(screen.getByPlaceholderText("Add a short note for the recipients"), "  Look at this ");
    await userEvent.click(screen.getByRole("button", { name: "Send notes" }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalled());
    expect(mocks.create).toHaveBeenCalledWith(7, {
      recipients: [
        { email: "daniel@example.test", name: "Daniel" },
        { email: "client@example.test", name: null },
      ],
      language: "fr",
      attach: "pdf",
      include_transcript: true,
      message: "Look at this",
    });
    await waitFor(() => expect(onShared).toHaveBeenCalledWith(created, "fr", 2));
    expect(onClose).toHaveBeenCalled();
  });

  it("keeps the dialog open and shows the API's words when the day's limit is used", async () => {
    const refusal = shareCapture("37-share-limit.json").response.body as { error: { code: string; message: string } };
    mocks.create.mockRejectedValue(new ApiError(429, refusal.error.code, refusal.error.message));
    const { onShared, onClose } = open();
    await userEvent.click(screen.getByRole("button", { name: "Send notes" }));
    expect(await screen.findByText(refusal.error.message)).toBeTruthy();
    expect(onShared).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("the toast", () => {
  it("says 'Notes sent to N people', 'in French' for another language, and singular for one", () => {
    expect(sentToast(created, "en", 3)).toBe("Notes sent to 3 people");
    expect(sentToast(created, "fr", 1)).toBe("Notes sent in French to 1 person");
    expect(sentToast(created, "es", 2)).toBe("Notes sent in Spanish to 2 people");
  });
  it("says the notes are being translated when the share is waiting for it", () => {
    expect(sentToast(waiting, "fr", 2)).toBe("Translating the notes to French. They will be sent to 2 people when ready");
  });
});

describe("the meeting page", () => {
  const templates: Template[] = [{ id: 1, name: "General", instructions: "g" }];
  const ready = captured<Meeting>("47-meeting-detail-ready.json");
  const draw = (meeting: Meeting, shares: Share[] = []) =>
    render(
      <TimeZoneProvider value={null}>
        <MeetingView initial={meeting} templates={templates} members={members} initialShares={shares} />
      </TimeZoneProvider>,
    );

  it("has a Share notes button for the owner of a ready meeting, and the dialog opens from it", async () => {
    draw({ ...ready, can_share: true, participants: ["Daniel"] });
    await userEvent.click(screen.getByRole("button", { name: /Share notes/ }));
    expect(screen.getByRole("heading", { name: "Share notes" })).toBeTruthy();
  });

  it("has no Share notes button for a teammate reading a shared meeting", () => {
    draw({ ...ready, can_share: false });
    expect(screen.queryByRole("button", { name: /Share notes/ })).toBeNull();
  });

  it("writes 'Shared with <names>' above the participants from the shares", () => {
    draw({ ...ready, participants: ["Daniel"] }, listed);
    const note = screen.getByText(/^Shared with /);
    expect(note.textContent).toBe("Shared with Client Example, Daniel Example");
    const side = within(note.closest("aside") as HTMLElement);
    expect(side.getByText("Participants")).toBeTruthy();
  });

  it("leaves out a recipient whose mail did not go", () => {
    const share = { ...listed[0], recipients: [{ ...listed[0].recipients[0], status: "not_sent" as const }, listed[0].recipients[1]] };
    draw(ready, [share]);
    expect(screen.getByText(/^Shared with /).textContent).toBe("Shared with Daniel Example");
  });
});

describe("the unsubscribe page", () => {
  it("posts the token only when the button is pressed, then says what stops", async () => {
    mocks.optOut.mockResolvedValue({ status: "unsubscribed" });
    render(<UnsubscribeForm />);
    expect(mocks.optOut).not.toHaveBeenCalled(); // opening the link does nothing
    await userEvent.click(screen.getByRole("button", { name: "Stop these emails" }));
    expect(mocks.optOut).toHaveBeenCalledWith("tok");
    expect(await screen.findByText("You will not get these emails")).toBeTruthy();
    expect(screen.getByText(/still arrive/)).toBeTruthy();
  });

  it("says the link does not work when the API says the token is used up", async () => {
    const dead = shareCapture("15-optout-used-twice.json").response.body as { error: { code: string; message: string } };
    mocks.optOut.mockRejectedValue(new ApiError(400, dead.error.code, dead.error.message));
    render(<UnsubscribeForm />);
    await userEvent.click(screen.getByRole("button", { name: "Stop these emails" }));
    expect(await screen.findByText("This link does not work")).toBeTruthy();
  });

  it("says so when there is no token in the address", () => {
    mocks.token.value = null;
    render(<UnsubscribeForm />);
    expect(screen.getByText("This link does not work")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Stop these emails" })).toBeNull();
  });
});
