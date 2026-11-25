// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BriefEditor } from "@/components/app/brief-editor";
import { ApiError } from "@/lib/api/client";
import type { Brief, BriefImported } from "@/lib/api/types";
import { recipesLibraryBody, recipesLibraryCapture } from "./helpers";

// Fed the bodies captured from the running API (docs/fe-captures/recipes-library/): the brief as an owner and
// as a member read it, the saved brief, the three file imports and the refusals.
const mocks = vi.hoisted(() => ({ save: vi.fn(), importFile: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { brief: { save: mocks.save, importFile: mocks.importFile } } }));

const empty = recipesLibraryBody<Brief>("38-brief-empty.json");
const saved = recipesLibraryBody<Brief>("39-brief-put.json");
const asMember = recipesLibraryBody<Brief>("40-brief-get-as-member.json");
const imported = {
  txt: recipesLibraryBody<BriefImported>("48-import-txt.json"),
  docx: recipesLibraryBody<BriefImported>("49-import-docx.json"),
  pdf: recipesLibraryBody<BriefImported>("50-import-pdf.json"),
};
const refusal = (file: string) => {
  const capture = recipesLibraryCapture(file);
  const { code, message } = (capture.response.body as { error: { code: string; message: string } }).error;
  return new ApiError(capture.response.status, code, message);
};

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
  mocks.save.mockImplementation(async (text: string) => ({ ...saved, text, updated_at: "2026-11-24T15:00:00Z" }));
});
afterEach(cleanup);

const box = () => screen.getByLabelText(/^(My|Workspace) brief$/) as HTMLTextAreaElement;
const file = (name = "prices.docx") => new File(["x"], name);
const choose = (f: File) => userEvent.upload(screen.getByLabelText("Choose a file to import"), f);

describe("the brief", () => {
  it("shows the brief, whose it is, who may read it, and how much of the cap is used", () => {
    render(<BriefEditor initial={saved} />);
    expect(screen.getByRole("heading", { name: "My brief" })).toBeTruthy();
    expect(box().value).toBe(saved.text);
    expect(screen.getByText(`${saved.text.length} of 12,000 characters`)).toBeTruthy();
    expect(screen.getByText(/Only you can read this/)).toBeTruthy();
    expect(screen.getByText(/Facts about your business that Beyric can use/)).toBeTruthy();
    expect(screen.getByText(/Last saved by Ada Lovelace/)).toBeTruthy();
  });

  it("is the workspace's brief, read by everyone in it, outside a person's own space", () => {
    render(<BriefEditor initial={asMember} />);
    expect(screen.getByRole("heading", { name: "Workspace brief" })).toBeTruthy();
    expect(screen.getByText(new RegExp(`Everyone on ${asMember.team.name} can read this`))).toBeTruthy();
    expect(screen.queryByText(/Only you can read this/)).toBeNull();
  });

  it("saves the edited text and says so", async () => {
    render(<BriefEditor initial={empty} />);
    expect((screen.getByRole("button", { name: "Save brief" }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.type(box(), "Audits from 400 USD.");
    await userEvent.click(screen.getByRole("button", { name: "Save brief" }));
    await waitFor(() => expect(mocks.save).toHaveBeenCalledWith("Audits from 400 USD."));
    expect(await screen.findByText("Saved")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Save brief" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows what was stored, not what was typed", async () => {
    mocks.save.mockResolvedValue({ ...saved, text: "Cleaned text" });
    render(<BriefEditor initial={empty} />);
    await userEvent.type(box(), "Typed text");
    await userEvent.click(screen.getByRole("button", { name: "Save brief" }));
    await waitFor(() => expect(box().value).toBe("Cleaned text"));
  });

  it("will not save a brief over the cap, and the counter says by how much", async () => {
    render(<BriefEditor initial={{ ...empty, max_chars: 10 }} />);
    await userEvent.type(box(), "twelve chars");
    expect(screen.getByText("12 of 10 characters. 2 too many.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Save brief" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows the server's words when it refuses the save", async () => {
    mocks.save.mockRejectedValue(refusal("43-brief-too-long.json"));
    render(<BriefEditor initial={empty} />);
    await userEvent.type(box(), "x");
    await userEvent.click(screen.getByRole("button", { name: "Save brief" }));
    expect(await screen.findByText("The brief can hold 12000 characters; this one has 12001. Shorten it.")).toBeTruthy();
    expect(screen.queryByText("Saved")).toBeNull();
  });

  it("a member reads it and cannot change it, save it or import into it", () => {
    render(<BriefEditor initial={asMember} />);
    expect(box().readOnly).toBe(true);
    expect(screen.queryByRole("button", { name: "Save brief" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Import from a file" })).toBeNull();
    expect(screen.getByText(/Only an owner or admin of the team can change the brief/)).toBeTruthy();
  });

  it("a team with no brief says what one is for", () => {
    render(<BriefEditor initial={empty} />);
    expect(screen.getByText(/prices, terms, house style/i)).toBeTruthy();
  });
});

describe("importing a file", () => {
  it("takes .docx, .pdf, .txt and .md", () => {
    render(<BriefEditor initial={empty} />);
    expect(screen.getByLabelText("Choose a file to import").getAttribute("accept")).toBe(".docx,.pdf,.txt,.md");
  });

  it("shows the imported text for review, changes nothing and saves nothing", async () => {
    mocks.importFile.mockResolvedValue(imported.docx);
    render(<BriefEditor initial={saved} />);
    const f = file();
    await choose(f);
    expect(mocks.importFile).toHaveBeenCalledWith(f);
    const review = await screen.findByRole("group", { name: "Imported text" });
    expect((within(review).getByLabelText("Imported text") as HTMLTextAreaElement).value).toBe(imported.docx.text);
    expect(screen.getByText(/not saved yet/i)).toBeTruthy();
    expect(box().value).toBe(saved.text);
    expect(mocks.save).not.toHaveBeenCalled();
  });

  it("lets the person edit the imported text, put it in place of the brief, then save", async () => {
    mocks.importFile.mockResolvedValue(imported.pdf);
    render(<BriefEditor initial={saved} />);
    await choose(file("brief.pdf"));
    const review = await screen.findByRole("group", { name: "Imported text" });
    const text = within(review).getByLabelText("Imported text") as HTMLTextAreaElement;
    await userEvent.clear(text);
    await userEvent.type(text, "Only the price list.");
    await userEvent.click(within(review).getByRole("button", { name: "Replace the brief" }));
    expect(box().value).toBe("Only the price list.");
    expect(screen.queryByRole("group", { name: "Imported text" })).toBeNull();
    expect(mocks.save).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Save brief" }));
    await waitFor(() => expect(mocks.save).toHaveBeenCalledWith("Only the price list."));
  });

  it("can add the imported text after what is already there", async () => {
    mocks.importFile.mockResolvedValue(imported.txt);
    render(<BriefEditor initial={saved} />);
    await choose(file("terms.txt"));
    await userEvent.click(await screen.findByRole("button", { name: "Add to the end" }));
    expect(box().value).toBe(`${saved.text}\n\n${imported.txt.text}`);
  });

  it("can be thrown away", async () => {
    mocks.importFile.mockResolvedValue(imported.txt);
    render(<BriefEditor initial={saved} />);
    await choose(file("terms.txt"));
    await userEvent.click(await screen.findByRole("button", { name: "Discard" }));
    expect(screen.queryByRole("group", { name: "Imported text" })).toBeNull();
    expect(box().value).toBe(saved.text);
  });

  it("says when the file held more than a brief may", async () => {
    mocks.importFile.mockResolvedValue({ ...imported.txt, truncated: true });
    render(<BriefEditor initial={saved} />);
    await choose(file("terms.txt"));
    expect(await screen.findByText(/Only the first 12,000 characters were imported/)).toBeTruthy();
  });

  it("says how many pages a PDF had", async () => {
    mocks.importFile.mockResolvedValue(imported.pdf);
    render(<BriefEditor initial={saved} />);
    await choose(file("brief.pdf"));
    expect(await screen.findByText(/2 pages/)).toBeTruthy();
  });

  it.each([
    ["51-import-scanned-pdf.json", "There is no text we can read in that file. A scanned PDF has none: paste the text in instead."],
    ["52-import-too-many-pages.json", "That PDF has too many pages. Paste the part you need instead."],
    ["53-import-unsupported.json", "Beyric reads .docx, .pdf, .txt and .md files. Save the file in one of these."],
    ["55-import-too-big.json", "That file is too big. Try a smaller one, or paste the text in."],
  ])("shows the server's words for the refusal in %s", async (capture, message) => {
    mocks.importFile.mockRejectedValue(refusal(capture));
    render(<BriefEditor initial={saved} />);
    await choose(file());
    expect(await screen.findByText(message)).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Imported text" })).toBeNull();
    expect(box().value).toBe(saved.text);
  });

  it("can import the same file again after a refusal", async () => {
    mocks.importFile.mockRejectedValueOnce(refusal("53-import-unsupported.json")).mockResolvedValueOnce(imported.txt);
    render(<BriefEditor initial={saved} />);
    await choose(file("a.pdf"));
    await screen.findByText(/Beyric reads/);
    await choose(file("a.pdf"));
    expect(await screen.findByRole("group", { name: "Imported text" })).toBeTruthy();
  });
});
