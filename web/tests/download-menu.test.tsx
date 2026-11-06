// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MeetingView } from "@/components/app/meeting-view";
import { ApiError } from "@/lib/api/client";
import type { Meeting, Template } from "@/lib/api/types";
import { captured, read, REPO } from "./helpers";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

// The Download menu on the meeting page. Meetings are the bodies captured from the running API
// (docs/fe-captures/owner-app/); the errors are the ones the export endpoint answered with
// (docs/fe-captures/export/, `python scripts/capture_export_api.py`).

const mocks = vi.hoisted(() => ({ get: vi.fn(), export: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({
  api: { milestones: { forMeeting: () => Promise.resolve({ milestones: [] }), confirm: vi.fn() }, meetings: { get: mocks.get, export: mocks.export } },
}));

const templates: Template[] = [{ id: 1, name: "General", instructions: "g" }];
const meeting = (file: string) => captured<Meeting>(file);
const show = (m: Meeting) =>
  render(<MeetingView initial={m} templates={templates} />);

type Capture = {
  response: {
    status: number;
    body: { error: { code: string; message: string } };
  };
};
function failure(file: string): ApiError {
  const { status, body } = (
    JSON.parse(read(join(REPO, "docs/fe-captures/export", file))) as Capture
  ).response;
  return new ApiError(status, body.error.code, body.error.message);
}

const FILE = {
  blob: new Blob(["%PDF-1.7"], { type: "application/pdf" }),
  filename: "Google Meet abc-defg-hij.pdf",
};
const created: string[] = [];
let saved: { download: string; href: string }[] = [];

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
  saved = [];
  created.length = 0;
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: (blob: Blob) => {
      created.push(String(blob.size));
      return "blob:placeholder";
    },
    revokeObjectURL: () => undefined,
  });
  // jsdom cannot navigate: record what the page would have downloaded instead.
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    saved.push({ download: this.download, href: this.href });
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const READY_ID = meeting("47-meeting-detail-ready.json").id;
const statusText = () => document.querySelector(".dl-status")?.textContent;
const open = () => userEvent.click(screen.getByText("Download"));

describe("the Download menu", () => {
  it("offers PDF and Word, and the transcript when the meeting has one", async () => {
    show(meeting("47-meeting-detail-ready.json"));
    await open();
    expect(
      screen.getByRole("button", { name: /PDF Best for reading and printing/ }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", {
        name: /Word document Edit in Word or Google Docs/,
      }),
    ).toBeTruthy();
    expect(
      (screen.getByLabelText("Include the full transcript") as HTMLInputElement)
        .checked,
    ).toBe(false);
  });

  it("downloads a PDF without the transcript, says it is preparing, then names the file", async () => {
    let finish: (file: typeof FILE) => void = () => undefined;
    mocks.export.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    show(meeting("47-meeting-detail-ready.json"));
    await open();
    await userEvent.click(screen.getByRole("button", { name: /PDF Best for/ }));
    expect(mocks.export).toHaveBeenCalledWith(READY_ID, "pdf", false, "en");
    expect(statusText()).toContain("Preparing PDF...");
    expect(screen.getAllByText(/Preparing PDF/).length).toBeGreaterThan(0); // the button says so too
    finish(FILE);
    await waitFor(() =>
      expect(statusText()).toBe("Google Meet abc-defg-hij.pdf downloaded"),
    );
    expect(saved).toEqual([
      { download: "Google Meet abc-defg-hij.pdf", href: "blob:placeholder" },
    ]);
  });

  it("asks for Word with the transcript when the box is ticked", async () => {
    mocks.export.mockResolvedValue({
      ...FILE,
      filename: "Google Meet abc-defg-hij.docx",
    });
    show(meeting("47-meeting-detail-ready.json"));
    await open();
    await userEvent.click(screen.getByLabelText("Include the full transcript"));
    await userEvent.click(
      screen.getByRole("button", { name: /Word document/ }),
    );
    expect(mocks.export).toHaveBeenCalledWith(READY_ID, "docx", true, "en");
  });

  it("names the file itself when the server sent no name", async () => {
    mocks.export.mockResolvedValue({ ...FILE, filename: null });
    show(meeting("47-meeting-detail-ready.json"));
    await open();
    await userEvent.click(screen.getByRole("button", { name: /PDF Best for/ }));
    await waitFor(() =>
      expect(saved[0]?.download).toBe("Google Meet abc-defg-hij.pdf"),
    );
  });

  it("shows the server's message when another file is being prepared", async () => {
    mocks.export.mockRejectedValue(failure("16-export-busy.json"));
    show(meeting("47-meeting-detail-ready.json"));
    await open();
    await userEvent.click(screen.getByRole("button", { name: /PDF Best for/ }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Another file is being prepared. Try again in a few seconds.",
    );
    expect(saved).toEqual([]);
  });

  it("shows the time-out message, and can be tried again", async () => {
    mocks.export
      .mockRejectedValueOnce(failure("17-export-timeout.json"))
      .mockResolvedValueOnce(FILE);
    show(meeting("47-meeting-detail-ready.json"));
    await open();
    await userEvent.click(screen.getByLabelText("Include the full transcript"));
    await userEvent.click(screen.getByRole("button", { name: /PDF Best for/ }));
    expect((await screen.findByRole("alert")).textContent).toContain(
      "took too long",
    );
    await open();
    await userEvent.click(screen.getByRole("button", { name: /PDF Best for/ }));
    await waitFor(() => expect(statusText()).toContain("downloaded"));
    expect(mocks.export).toHaveBeenCalledTimes(2);
  });

  it("shows a failure that is not a known code as the plain message", async () => {
    mocks.export.mockRejectedValue(
      new ApiError(
        0,
        "network_error",
        "Could not reach the server. Check your connection and try again.",
      ),
    );
    show(meeting("47-meeting-detail-ready.json"));
    await open();
    await userEvent.click(
      screen.getByRole("button", { name: /Word document/ }),
    );
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Could not reach the server",
    );
  });

  it("refetches the meeting when the server says the notes are not ready", async () => {
    mocks.export.mockRejectedValue(
      failure("10-export-not-ready-processing.json"),
    );
    mocks.get.mockResolvedValue(meeting("43-meeting-detail-processing.json"));
    show(meeting("47-meeting-detail-ready.json"));
    await open();
    await userEvent.click(screen.getByRole("button", { name: /PDF Best for/ }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "The notes are not ready yet.",
    );
    await waitFor(() => expect(mocks.get).toHaveBeenCalledWith(READY_ID));
  });

  it("is a disabled button, with its reason, while the notes are being written", () => {
    show(meeting("43-meeting-detail-processing.json"));
    const button = screen.getByRole("button", {
      name: /Download/,
    }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("aria-describedby")).toBe("dl-why");
    expect(document.getElementById("dl-why")?.textContent).toBe(
      "Available when the notes are ready.",
    );
    expect(screen.queryByText("Word document")).toBeNull();
  });

  it("is not shown for a failed meeting, which has no notes", () => {
    show(meeting("50-meeting-detail-failed-no-speech.json"));
    expect(screen.queryByText("Download")).toBeNull();
  });
});
