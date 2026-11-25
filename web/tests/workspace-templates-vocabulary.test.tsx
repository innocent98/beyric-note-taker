// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TemplatesEditor } from "@/components/app/templates-editor";
import { VocabularyManager } from "@/components/app/vocabulary-manager";

// Templates and vocabulary belong to the workspace (workspaces design, section 3): members read and
// use them, owners and admins write them, and a team workspace's manager can copy the personal ones
// in. The payloads are the shapes captured from the running app (docs/fe-captures/workspaces/).

const mocks = vi.hoisted(() => ({ copyTemplates: vi.fn(), copyTerms: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({
  api: {
    templates: { copyFromPersonal: mocks.copyTemplates, create: vi.fn(), update: vi.fn(), remove: vi.fn() },
    vocabulary: { copyFromPersonal: mocks.copyTerms, add: vi.fn(), list: vi.fn(), remove: vi.fn() },
  },
}));

const TEMPLATES = [
  { id: 1, name: "Board", instructions: "List the votes." },
  { id: 2, name: "General", instructions: "Write a summary." },
];
const TERMS = [{ id: 5, term: "Luran" }];
const MEMBER = { name: "Luran", isPersonal: false, canEdit: false };
const MANAGER = { name: "Luran", isPersonal: false, canEdit: true };

beforeEach(() => {
  mocks.copyTemplates.mockReset();
  mocks.copyTerms.mockReset();
});
afterEach(cleanup);

describe("templates", () => {
  it("a member reads the list and the chosen template, with no way to change anything", () => {
    render(<TemplatesEditor initial={TEMPLATES} workspace={MEMBER} />);
    expect(screen.getByText("Only owners and admins of Luran can change its templates. You can use them for your meetings.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Add template/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete template" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy from my personal workspace" })).toBeNull();
    expect((screen.getByLabelText(/Name/) as HTMLInputElement).readOnly).toBe(true);
    expect((screen.getByLabelText(/Instructions/) as HTMLTextAreaElement).readOnly).toBe(true);
    expect(screen.getByRole("button", { name: /General/ })).toBeTruthy();
  });

  it("a manager of a team workspace can copy the personal ones in, and is told what happened", async () => {
    mocks.copyTemplates.mockResolvedValue({
      copied: 1,
      skipped: 2,
      templates: [...TEMPLATES, { id: 9, name: "Custom", instructions: "mine" }],
    });
    render(<TemplatesEditor initial={TEMPLATES} workspace={MANAGER} />);
    await userEvent.click(screen.getByRole("button", { name: "Copy from my personal workspace" }));
    expect(mocks.copyTemplates).toHaveBeenCalledOnce();
    expect(await screen.findByText("Copied 1 template from your personal workspace. 2 already here, left as they were.")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Custom/ })).toBeTruthy();
  });

  it("says plainly when there is nothing to copy", async () => {
    mocks.copyTemplates.mockResolvedValue({ copied: 0, skipped: 2, templates: TEMPLATES });
    render(<TemplatesEditor initial={TEMPLATES} workspace={MANAGER} />);
    await userEvent.click(screen.getByRole("button", { name: "Copy from my personal workspace" }));
    expect(await screen.findByText("Nothing to copy: every personal template is already here.")).toBeTruthy();
  });

  it("the personal workspace has no copy button and is fully editable", () => {
    render(<TemplatesEditor initial={TEMPLATES} workspace={{ name: "your personal workspace", isPersonal: true, canEdit: true }} />);
    expect(screen.queryByRole("button", { name: "Copy from my personal workspace" })).toBeNull();
    expect(screen.getByRole("button", { name: /Add template/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  });
});

describe("vocabulary", () => {
  it("a member reads the list without the add form or the remove buttons", () => {
    render(<VocabularyManager initial={TERMS} workspace={MEMBER} />);
    expect(screen.getByText("Only owners and admins of Luran can change its vocabulary. It is used for every meeting recorded there.")).toBeTruthy();
    expect(screen.getByText("Luran")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add term" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove Luran" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy from my personal workspace" })).toBeNull();
  });

  it("a manager copies the personal terms in", async () => {
    mocks.copyTerms.mockResolvedValue({ copied: 1, skipped: 1, terms: [...TERMS, { id: 6, term: "Beyric" }] });
    render(<VocabularyManager initial={TERMS} workspace={MANAGER} />);
    await userEvent.click(screen.getByRole("button", { name: "Copy from my personal workspace" }));
    expect(await screen.findByText("Copied 1 term from your personal workspace. 1 already here, left as they were.")).toBeTruthy();
    expect(screen.getByText("Beyric")).toBeTruthy();
  });

  it("without a workspace given it behaves as before (personal)", () => {
    render(<VocabularyManager initial={TERMS} />);
    expect(screen.getByRole("button", { name: "Add term" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Remove Luran" })).toBeTruthy();
  });
});
