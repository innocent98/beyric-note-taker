// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DraftsGoWithIt } from "@/components/app/meeting-documents";

// Spec 5.4a: deleting a meeting deletes the person's documents made from it alone and keeps the ones made
// from several meetings; the confirmation says so. Rows are shaped like the list the API answers
// (`access`, `meeting_ids`); only those two fields are read.

const mocks = vi.hoisted(() => ({ list: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { documents: { list: mocks.list } } }));

const row = (id: number, meeting_ids: number[], access = "owner") => ({ id, meeting_ids, access });

beforeEach(() => {
  mocks.list.mockReset(); // in braces: a beforeEach that returns a function has it called as its cleanup
});
afterEach(cleanup);

describe("what the delete confirmation says about documents", () => {
  it("says nothing when the meeting has no documents", async () => {
    mocks.list.mockResolvedValue({ documents: [] });
    const { container } = render(<DraftsGoWithIt meetingId={7} />);
    await Promise.resolve();
    expect(container.textContent).toBe("");
  });

  it("names the documents made only from this meeting", async () => {
    mocks.list.mockResolvedValue({ documents: [row(1, [7]), row(2, [7])] });
    render(<DraftsGoWithIt meetingId={7} />);
    expect(await screen.findByText("This also deletes the 2 documents made only from it.")).toBeTruthy();
    expect(mocks.list).toHaveBeenCalledWith({ meetingId: 7, limit: 100 });
  });

  it("says one document in the singular, and that a several-meeting document stays", async () => {
    mocks.list.mockResolvedValue({ documents: [row(1, [7]), row(2, [7, 8])] });
    render(<DraftsGoWithIt meetingId={7} />);
    const text = (await screen.findByText(/This also deletes the document made only from it/)).textContent;
    expect(text).toContain("A document made from several meetings stays; its link to this meeting will read not available.");
  });

  it("does not count a teammate's shared draft, which is theirs and is not what this deletes", async () => {
    mocks.list.mockResolvedValue({ documents: [row(1, [7], "shared")] });
    const { container } = render(<DraftsGoWithIt meetingId={7} />);
    await Promise.resolve();
    await Promise.resolve();
    expect(container.textContent).toBe("");
  });

  it("draws nothing when the list cannot be read (the delete still asks its own question)", async () => {
    mocks.list.mockImplementation(() => {
      throw new TypeError("offline");
    });
    const { container } = render(<DraftsGoWithIt meetingId={7} />);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(mocks.list).toHaveBeenCalledTimes(1);
    expect(container.textContent).toBe("");
  });
});
