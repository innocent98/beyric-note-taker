// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RecordingAckCard } from "@/components/app/recording-ack-card";
import { ApiError } from "@/lib/api/client";
import { recordingCapture } from "./helpers";

// The shapes come from responses captured against the running app (docs/fe-captures/recording-ack/).

const mocks = vi.hoisted(() => ({ recordingAck: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { settings: { recordingAck: mocks.recordingAck } } }));

const TEXT =
  "I understand that I'm responsible for telling the people in my meetings that Beyric Note Taker will record and transcribe them, and for getting their consent wherever the law requires it.";

beforeEach(() => {
  mocks.recordingAck.mockReset(); // in braces: a returned function would be run as a teardown
});

describe("the recording card on the Meetings page", () => {
  it("is driven by the captured session flag: false draws it, true draws nothing", () => {
    const old = recordingCapture("20-login-olu.json").response.body as { user: { recording_acknowledged: boolean } };
    const signedUp = recordingCapture("09-login-ada.json").response.body as { user: { recording_acknowledged: boolean } };
    expect(old.user.recording_acknowledged).toBe(false);
    expect(signedUp.user.recording_acknowledged).toBe(true);
    const { container, unmount } = render(<RecordingAckCard acknowledged={old.user.recording_acknowledged} />);
    expect(screen.getByText(TEXT)).toBeTruthy();
    unmount();
    const again = render(<RecordingAckCard acknowledged={signedUp.user.recording_acknowledged} />);
    expect(again.container.textContent).toBe("");
    expect(container).toBeTruthy();
  });

  it("stores the acknowledgement with one click and goes away", async () => {
    mocks.recordingAck.mockResolvedValue(recordingCapture("24-ack.json").response.body);
    const user = userEvent.setup();
    render(<RecordingAckCard acknowledged={false} />);
    expect((screen.getByRole("button", { name: "How to tell attendees" }) as HTMLButtonElement).type).toBe("button");
    await user.click(screen.getByRole("button", { name: "I understand" }));
    expect(mocks.recordingAck).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByText(TEXT)).toBeNull());
  });

  it("keeps the card and says what went wrong when saving fails, and never blocks the page", async () => {
    mocks.recordingAck.mockRejectedValue(new ApiError(500, "internal_error", "Something went wrong. Please try again.", []));
    const user = userEvent.setup();
    render(<RecordingAckCard acknowledged={false} />);
    await user.click(screen.getByRole("button", { name: "I understand" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Something went wrong");
    expect(screen.getByText(TEXT)).toBeTruthy();
    expect((screen.getByRole("button", { name: "I understand" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
