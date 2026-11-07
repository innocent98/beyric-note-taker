// @vitest-environment jsdom
import { markSetupPending, takeSetupPending } from "@/lib/setup-pending";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LoginForm } from "@/components/app/login-form";
import { MeetingList } from "@/components/app/meeting-list";
import { NewMeetingForm } from "@/components/app/new-meeting-form";
import { TemplatesEditor } from "@/components/app/templates-editor";
import { VocabularyManager } from "@/components/app/vocabulary-manager";
import { ApiError } from "@/lib/api/client";
import type { Meeting, MeetingList as MeetingListData, Template, Term } from "@/lib/api/types";
import { captured, capturedDelete } from "./helpers";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  refresh: vi.fn(),
  login: vi.fn(),
  create: vi.fn(),
  list: vi.fn(),
  tplCreate: vi.fn(),
  tplUpdate: vi.fn(),
  tplRemove: vi.fn(),
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
    templates: { create: mocks.tplCreate, update: mocks.tplUpdate, remove: mocks.tplRemove },
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
    // The designer's field-level lines under the inputs, not one alert above the form.
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByLabelText("Work email").closest(".field")?.textContent).toContain("Enter your email.");
    expect(screen.getByLabelText("Password").closest(".field")?.textContent).toContain("Enter your password.");
    await userEvent.type(screen.getByLabelText("Work email"), "owner");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByLabelText("Work email").closest(".field")?.textContent).toContain("Enter a valid email, like you@company.com.");
    await userEvent.clear(screen.getByLabelText("Work email"));
    await userEvent.type(screen.getByLabelText("Work email"), "owner@example.test");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByLabelText("Work email").closest(".field")?.textContent).not.toContain("Enter");
    expect(screen.getByLabelText("Password").closest(".field")?.textContent).toContain("Enter your password.");
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

  it("sends a new account's first sign-in to the workspace step, once", async () => {
    mocks.login.mockResolvedValue({ authenticated: true, csrf_token: "x", user: null, usage: null });
    markSetupPending();
    render(<LoginForm />);
    await userEvent.type(screen.getByLabelText("Work email"), "owner@example.test");
    await userEvent.type(screen.getByLabelText("Password"), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(mocks.replace).toHaveBeenCalledWith("/setup");
    expect(takeSetupPending()).toBe(false);
  });

  it("shows the captured wrong-credentials message and lets the user try again", async () => {
    mocks.login.mockRejectedValue(new ApiError(401, "invalid_credentials", "That email or password is not right."));
    render(<LoginForm />);
    await userEvent.type(screen.getByLabelText("Work email"), "owner@example.test");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    // The API answers an unconfirmed account like a wrong password, so the form adds a line about it.
    expect((await screen.findByRole("alert")).textContent).toBe(
      "That email or password is not right.If you have just signed up, confirm your email first: check your inbox for the link.",
    );
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

  it("shows the name a meeting's template had when that template is no longer listed", () => {
    const list = captured<MeetingListData>("53-meetings-list.json");
    const gone = { ...list, meetings: list.meetings.map((m, i) => (i === 0 ? { ...m, template_id: 99, template_name: "Interview" } : m)) };
    render(<MeetingList initial={gone as MeetingListData} templates={templates} />);
    expect(within(screen.getAllByRole("listitem")[0]).getByText("Interview")).toBeTruthy();
  });

  it("says 'Meeting deleted' once after a delete, then not again", () => {
    sessionStorage.setItem("beyric:meeting-deleted", "1");
    const list = captured<MeetingListData>("53-meetings-list.json");
    const { unmount } = render(<MeetingList initial={list} templates={templates} />);
    expect(screen.getByRole("status").textContent).toContain("Meeting deleted");
    expect(sessionStorage.getItem("beyric:meeting-deleted")).toBeNull();
    unmount();
    render(<MeetingList initial={list} templates={templates} />);
    expect(screen.getByRole("status").textContent).toBe("");
  });

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

  it("shows what the bot is doing on a meeting still in its call", () => {
    render(<MeetingList initial={captured<MeetingListData>("110-meetings-list-bot-needs-help.json")} templates={templates} />);
    const first = screen.getAllByRole("listitem")[0];
    expect(within(first).getByText(/waiting on a prompt in the meeting.*Check the meeting for a prompt\./)).toBeTruthy();
    expect(within(first).queryByText("Bot sent")).toBeNull();
  });

  it("asks the server for a fresh list every refresh_after_seconds while a meeting is being worked on", async () => {
    vi.useFakeTimers();
    try {
      const busy = captured<MeetingListData>("110-meetings-list-bot-needs-help.json");
      expect(busy.refresh_after_seconds).toBe(30);
      render(<MeetingList initial={busy} templates={templates} />);
      await act(async () => vi.advanceTimersByTime(29_000));
      expect(mocks.refresh).not.toHaveBeenCalled();
      await act(async () => vi.advanceTimersByTime(1_500));
      expect(mocks.refresh).toHaveBeenCalledTimes(1);
      await act(async () => vi.advanceTimersByTime(30_000));
      expect(mocks.refresh).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops asking once nothing listed is being worked on", async () => {
    vi.useFakeTimers();
    try {
      const idle = captured<MeetingListData>("54-meetings-list-page-1.json"); // all failed
      expect(idle.refresh_after_seconds).toBeNull();
      render(<MeetingList initial={idle} templates={templates} />);
      await act(async () => vi.advanceTimersByTime(120_000));
      expect(mocks.refresh).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not ask while the tab is hidden, and asks when it comes back", async () => {
    vi.useFakeTimers();
    try {
      Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
      render(<MeetingList initial={captured<MeetingListData>("110-meetings-list-bot-needs-help.json")} templates={templates} />);
      await act(async () => vi.advanceTimersByTime(90_000));
      expect(mocks.refresh).not.toHaveBeenCalled();
      Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
      await act(async () => document.dispatchEvent(new Event("visibilitychange")));
      expect(mocks.refresh).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
      Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    }
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

describe("deleting a template", () => {
  // The messages are the ones the running API answered (docs/fe-captures/template-delete/).
  const lastBody = capturedDelete("25-delete-last.json").response.body as { error: { code: string; message: string } };
  const deleteButton = () => screen.getByRole("button", { name: "Delete template" });
  const names = () => screen.getAllByRole("button").filter((b) => b.classList.contains("pick")).map((b) => b.querySelector(".pick-name")?.textContent);

  beforeEach(() => {
    mocks.tplRemove.mockReset(); // not returned: a returned function would run as a cleanup
  });

  it("draws the red text button on the left of the foot and Save on its right", () => {
    render(<TemplatesEditor initial={templates} />);
    const foot = deleteButton().closest(".form-foot")!;
    expect(foot.firstElementChild).toBe(deleteButton().closest(".tpl-foot-left") ?? deleteButton());
    expect(foot.querySelector(".tpl-foot-right")?.textContent).toContain("Save");
    expect(deleteButton().getAttribute("type")).toBe("button");
  });

  it("asks first, and Cancel sends nothing", async () => {
    render(<TemplatesEditor initial={templates} />);
    await userEvent.click(deleteButton());
    const ask = screen.getByRole("group", { name: "Delete this template?" });
    expect(ask.textContent).toContain("Delete this template? Meetings already written with it keep their notes.");
    expect(mocks.tplRemove).not.toHaveBeenCalled();
    await userEvent.click(within(ask).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "Delete this template?" })).toBeNull();
    expect(mocks.tplRemove).not.toHaveBeenCalled();
    expect(names()).toHaveLength(templates.length);
  });

  it("deletes the chosen template, drops it from the list and selects the first one", async () => {
    mocks.tplRemove.mockResolvedValue(undefined);
    render(<TemplatesEditor initial={templates} />);
    const second = templates.slice().sort((a, b) => a.name.localeCompare(b.name))[1];
    await userEvent.click(screen.getByRole("button", { name: new RegExp(second.name) }));
    await userEvent.click(deleteButton());
    await userEvent.click(within(screen.getByRole("group", { name: "Delete this template?" })).getByRole("button", { name: "Delete" }));
    expect(mocks.tplRemove).toHaveBeenCalledWith(second.id);
    await waitFor(() => expect(names()).not.toContain(second.name));
    expect(names()).toHaveLength(templates.length - 1);
    // the designer selects the first template of the list after a delete
    const first = templates.slice().sort((a, b) => a.name.localeCompare(b.name))[0];
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(first.name);
    expect(screen.queryByRole("group", { name: "Delete this template?" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("holds the buttons while the call is out", async () => {
    let finish: () => void = () => {};
    mocks.tplRemove.mockReturnValue(new Promise<void>((resolve) => (finish = resolve)));
    render(<TemplatesEditor initial={templates} />);
    await userEvent.click(deleteButton());
    const ask = screen.getByRole("group", { name: "Delete this template?" });
    await userEvent.click(within(ask).getByRole("button", { name: "Delete" }));
    expect((within(ask).getByRole("button", { name: "Deleting..." }) as HTMLButtonElement).disabled).toBe(true);
    expect((within(ask).getByRole("button", { name: "Cancel" }) as HTMLButtonElement).disabled).toBe(true);
    expect(mocks.tplRemove).toHaveBeenCalledTimes(1);
    await act(async () => finish());
  });

  it("says why for the last template (409) and keeps the list", async () => {
    mocks.tplRemove.mockRejectedValue(new ApiError(409, lastBody.error.code, lastBody.error.message));
    render(<TemplatesEditor initial={templates} />);
    await userEvent.click(deleteButton());
    await userEvent.click(within(screen.getByRole("group", { name: "Delete this template?" })).getByRole("button", { name: "Delete" }));
    expect((await screen.findByRole("alert")).textContent).toBe(lastBody.error.message);
    expect(names()).toHaveLength(templates.length);
    expect(screen.queryByRole("group", { name: "Delete this template?" })).toBeNull();
  });

  it("drops a template that is already gone (404) and says so plainly", async () => {
    mocks.tplRemove.mockRejectedValue(new ApiError(404, "not_found", "No such template"));
    render(<TemplatesEditor initial={templates} />);
    const first = templates.slice().sort((a, b) => a.name.localeCompare(b.name))[0];
    await userEvent.click(deleteButton());
    await userEvent.click(within(screen.getByRole("group", { name: "Delete this template?" })).getByRole("button", { name: "Delete" }));
    expect((await screen.findByRole("alert")).textContent).toBe("That template was already deleted.");
    expect(names()).not.toContain(first.name);
  });

  it("shows the network message when the server cannot be reached", async () => {
    mocks.tplRemove.mockRejectedValue(new ApiError(0, "network_error", "Could not reach the server. Check your connection and try again."));
    render(<TemplatesEditor initial={templates} />);
    await userEvent.click(deleteButton());
    await userEvent.click(within(screen.getByRole("group", { name: "Delete this template?" })).getByRole("button", { name: "Delete" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Could not reach the server. Check your connection and try again.");
    expect(names()).toHaveLength(templates.length);
  });

  it("closes the ask when another template is chosen", async () => {
    render(<TemplatesEditor initial={templates} />);
    await userEvent.click(deleteButton());
    expect(screen.getByRole("group", { name: "Delete this template?" })).toBeTruthy();
    await userEvent.click(screen.getAllByRole("button").filter((b) => b.classList.contains("pick"))[1]);
    expect(screen.queryByRole("group", { name: "Delete this template?" })).toBeNull();
  });

  it("discards an unsaved new template without asking or calling the server", async () => {
    render(<TemplatesEditor initial={templates} />);
    await userEvent.click(screen.getAllByRole("button", { name: "Add template" })[0]);
    expect(names()).toContain("New template");
    await userEvent.click(deleteButton());
    expect(screen.queryByRole("group", { name: "Delete this template?" })).toBeNull();
    expect(mocks.tplRemove).not.toHaveBeenCalled();
    expect(names()).not.toContain("New template");
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(
      templates.slice().sort((a, b) => a.name.localeCompare(b.name))[0].name,
    );
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
