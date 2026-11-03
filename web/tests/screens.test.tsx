// @vitest-environment jsdom
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LoginForm } from "@/components/app/login-form";
import { MeetingList } from "@/components/app/meeting-list";
import { NewMeetingForm } from "@/components/app/new-meeting-form";
import { TemplatesEditor } from "@/components/app/templates-editor";
import { VocabularyManager } from "@/components/app/vocabulary-manager";
import { ApiError } from "@/lib/api/client";
import type { Meeting, MeetingList as MeetingListData, Template, Term } from "@/lib/api/types";
import { captured } from "./helpers";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  login: vi.fn(),
  create: vi.fn(),
  list: vi.fn(),
  tplCreate: vi.fn(),
  tplUpdate: vi.fn(),
  termAdd: vi.fn(),
  termList: vi.fn(),
  termRemove: vi.fn(),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push, replace: mocks.replace, refresh: mocks.refresh }),
}));
vi.mock("@/lib/api/browser", () => ({
  api: {
    login: mocks.login,
    meetings: { create: mocks.create, list: mocks.list },
    templates: { create: mocks.tplCreate, update: mocks.tplUpdate },
    vocabulary: { add: mocks.termAdd, list: mocks.termList, remove: mocks.termRemove },
  },
}));

const templates = captured<{ templates: Template[] }>("11-templates-seeded.json").templates;

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
});

describe("login", () => {
  it("shows and hides the password, and asks for the email and the password before calling the API", async () => {
    render(<LoginForm />);
    expect(screen.getByLabelText("Work email")).toHaveProperty("type", "email");
    const field = screen.getByLabelText("Password");
    expect(field).toHaveProperty("type", "password");
    await userEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(field).toHaveProperty("type", "text");
    await userEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(field).toHaveProperty("type", "password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByRole("alert").textContent).toContain("Enter your email.");
    await userEvent.type(screen.getByLabelText("Work email"), "owner@example.test");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByRole("alert").textContent).toContain("Enter your password.");
    expect(mocks.login).not.toHaveBeenCalled();
  });

  it("signs in with the email and password, and goes to the app", async () => {
    mocks.login.mockResolvedValue({ authenticated: true, csrf_token: "x", user: null, usage: null });
    render(<LoginForm />);
    await userEvent.type(screen.getByLabelText("Work email"), "  owner@example.test ");
    await userEvent.type(screen.getByLabelText("Password"), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(mocks.login).toHaveBeenCalledWith("owner@example.test", "hunter2");
    expect(mocks.replace).toHaveBeenCalledWith("/");
  });

  it("shows the captured wrong-credentials message and lets the user try again", async () => {
    mocks.login.mockRejectedValue(new ApiError(401, "invalid_credentials", "That email or password is not right."));
    render(<LoginForm />);
    await userEvent.type(screen.getByLabelText("Work email"), "owner@example.test");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect((await screen.findByRole("alert")).textContent).toBe("That email or password is not right.");
    expect((screen.getByRole("button", { name: "Sign in" }) as HTMLButtonElement).disabled).toBe(false);
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("locks the form after the 15-minute lockout", async () => {
    mocks.login.mockRejectedValue(new ApiError(429, "too_many_attempts", "Too many failed attempts. Try again later."));
    render(<LoginForm />);
    await userEvent.type(screen.getByLabelText("Work email"), "owner@example.test");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/try again later/i);
    expect((screen.getByRole("button", { name: "Sign in" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("new meeting form", () => {
  it("sends the link and the chosen template, then opens the meeting", async () => {
    mocks.create.mockResolvedValue(captured<Meeting>("33-meeting-create.json"));
    render(<NewMeetingForm templates={templates} busy={false} />);
    await userEvent.type(screen.getByLabelText("Meeting link"), " https://meet.google.com/abc-defg-hij ");
    await userEvent.selectOptions(screen.getByLabelText("Notes template"), "2");
    await userEvent.click(screen.getByRole("button", { name: "Send note taker" }));
    expect(mocks.create).toHaveBeenCalledWith({ url: "https://meet.google.com/abc-defg-hij", template_id: 2 });
    expect(mocks.push).toHaveBeenCalledWith("/meetings/1");
    expect(screen.getByText("Tell participants that this meeting will be recorded.")).toBeTruthy();
  });

  it("keeps what was typed and shows the API's message when the link is refused", async () => {
    mocks.create.mockRejectedValue(
      new ApiError(400, "invalid_meeting_url", "That is not a Google Meet or Zoom link. Paste the full meeting link."),
    );
    render(<NewMeetingForm templates={templates} busy={false} />);
    const input = screen.getByLabelText("Meeting link") as HTMLInputElement;
    await userEvent.type(input, "https://example.com/x");
    await userEvent.click(screen.getByRole("button", { name: "Send note taker" }));
    expect(await screen.findByText(/That is not a Google Meet or Zoom link/)).toBeTruthy();
    expect(input.value).toBe("https://example.com/x");
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it("is locked, with the server's own sentence, while a bot is on a meeting", () => {
    render(<NewMeetingForm templates={templates} busy />);
    expect(screen.getByText("A bot is already on a meeting. Wait for it to finish first.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Send note taker" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("locks the button while the request runs", async () => {
    mocks.create.mockReturnValue(new Promise(() => undefined));
    render(<NewMeetingForm templates={templates} busy={false} />);
    await userEvent.type(screen.getByLabelText("Meeting link"), "https://meet.google.com/abc-defg-hij");
    await userEvent.click(screen.getByRole("button", { name: "Send note taker" }));
    expect((screen.getByRole("button", { name: "Sending..." }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("meeting list", () => {
  const page1 = captured<MeetingListData>("54-meetings-list-page-1.json");
  const page2 = captured<MeetingListData>("55-meetings-list-page-2.json");

  it("lists the captured meetings with status badges, template names and links", () => {
    render(<MeetingList initial={captured<MeetingListData>("53-meetings-list.json")} templates={templates} />);
    const rows = screen.getAllByRole("listitem");
    expect(rows.length).toBeGreaterThan(3);
    const first = rows[0];
    expect(within(first).getByText("Google Meet abc-defg-hij")).toBeTruthy();
    expect(within(first).getByText("Failed")).toBeTruthy();
    expect(within(first).getByText("General")).toBeTruthy();
    expect(within(first).getByRole("link").getAttribute("href")).toBe("/meetings/12");
    expect(screen.getByText(/^\d+ meetings?$/)).toBeTruthy();
  });

  it("shows the empty state", () => {
    render(<MeetingList initial={captured<MeetingListData>("10-meetings-empty.json")} templates={templates} />);
    expect(screen.getByText("No meetings yet.")).toBeTruthy();
    expect(screen.getByText("Paste a meeting link above to send the note taker.")).toBeTruthy();
  });

  it("loads older meetings with before_id until there are no more", async () => {
    mocks.list.mockResolvedValue({ ...page2, has_more: false, next_before_id: null });
    render(<MeetingList initial={page1} templates={templates} />);
    expect(screen.getByText("3 meetings+")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Show older meetings" }));
    expect(mocks.list).toHaveBeenCalledWith({ before_id: page1.next_before_id });
    expect(await screen.findByText("6 meetings")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Show older meetings" })).toBeNull();
  });
});

describe("templates", () => {
  it("edits the chosen template and shows Saved", async () => {
    mocks.tplUpdate.mockImplementation(async (id: number, body: object) => ({ id, ...body }));
    render(<TemplatesEditor initial={templates} />);
    // The list is sorted by name; the first is selected.
    const name = screen.getByLabelText("Name") as HTMLInputElement;
    expect(name.value).toBe("Client call");
    await userEvent.type(screen.getByLabelText("Instructions"), " More.");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(mocks.tplUpdate).toHaveBeenCalledWith(2, {
      name: "Client call",
      instructions: expect.stringMatching(/More\.$/),
    });
    expect((await screen.findByRole("status")).textContent).toBe("Saved");
  });

  it("adds a template and selects it", async () => {
    mocks.tplCreate.mockResolvedValue({ id: 5, name: "Standup", instructions: "One line per person." });
    render(<TemplatesEditor initial={templates} />);
    await userEvent.click(screen.getAllByRole("button", { name: "Add template" })[0]);
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("");
    await userEvent.type(screen.getByLabelText("Name"), "Standup");
    await userEvent.type(screen.getByLabelText("Instructions"), "One line per person.");
    await userEvent.click(screen.getAllByRole("button", { name: "Add template" }).pop()!);
    expect(mocks.tplCreate).toHaveBeenCalledWith({ name: "Standup", instructions: "One line per person." });
    expect(await screen.findByRole("button", { name: /Standup/ })).toBeTruthy();
  });

  it("shows the captured duplicate-name message", async () => {
    mocks.tplCreate.mockRejectedValue(new ApiError(400, "duplicate_name", "A template with that name already exists."));
    render(<TemplatesEditor initial={templates} />);
    await userEvent.click(screen.getAllByRole("button", { name: "Add template" })[0]);
    await userEvent.type(screen.getByLabelText("Name"), "General");
    await userEvent.type(screen.getByLabelText("Instructions"), "x");
    const submit = screen.getAllByRole("button", { name: "Add template" }).pop()!;
    await userEvent.click(submit);
    expect((await screen.findByRole("alert")).textContent).toBe("A template with that name already exists.");
  });
});

describe("vocabulary", () => {
  const terms = captured<{ terms: Term[] }>("19-vocabulary-list.json").terms;

  it("adds a term and shows the list the server returns", async () => {
    mocks.termAdd.mockResolvedValue({ id: 3, term: "Zed" });
    mocks.termList.mockResolvedValue([...terms, { id: 3, term: "Zed" }]);
    render(<VocabularyManager initial={terms} />);
    expect(screen.getByText("2 terms")).toBeTruthy();
    await userEvent.type(screen.getByLabelText("New term"), "  Zed ");
    await userEvent.click(screen.getByRole("button", { name: "Add term" }));
    expect(mocks.termAdd).toHaveBeenCalledWith("Zed");
    expect(await screen.findByText("3 terms")).toBeTruthy();
    expect((screen.getByLabelText("New term") as HTMLInputElement).value).toBe("");
  });

  it("shows the captured duplicate message", async () => {
    mocks.termAdd.mockRejectedValue(new ApiError(400, "duplicate_term", "That term is already in the list."));
    render(<VocabularyManager initial={terms} />);
    await userEvent.type(screen.getByLabelText("New term"), "Beyric");
    await userEvent.click(screen.getByRole("button", { name: "Add term" }));
    expect((await screen.findByRole("alert")).textContent).toBe("That term is already in the list.");
  });

  it("deletes a term, and treats a 404 (already gone) as success", async () => {
    mocks.termRemove.mockResolvedValueOnce(undefined).mockRejectedValueOnce(new ApiError(404, "not_found", "No such term"));
    render(<VocabularyManager initial={terms} />);
    await userEvent.click(screen.getByRole("button", { name: "Delete Beyric" }));
    expect(mocks.termRemove).toHaveBeenCalledWith(1);
    await act(async () => undefined);
    expect(screen.queryByText("Beyric")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Delete Acme Example" }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("No terms yet.")).toBeTruthy();
  });
});
