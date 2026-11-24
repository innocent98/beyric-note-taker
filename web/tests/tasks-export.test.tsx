// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TasksView } from "@/components/app/tasks-view";
import { ApiError, createApi } from "@/lib/api/client";
import type { TaskList } from "@/lib/api/types";
import { formatsCapture, tasksBody } from "./helpers";

// "Export CSV" on the Tasks screen (lead-designed) and the client call behind it. The file name and headers
// are the ones the API sent (docs/fe-captures/export-formats-and-search/17-tasks-csv.json).

const mocks = vi.hoisted(() => ({ exportCsv: vi.fn(), list: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/lib/api/browser", () => ({ api: { tasks: mocks } }));

const board = tasksBody<TaskList>("17-tasks.json");
const csv = formatsCapture("17-tasks-csv.json");
const NAME = /filename="([^"]+)"/.exec(csv.response.headers["content-disposition"])![1];
let saved: { download: string; href: string }[] = [];

beforeEach(() => {
  Object.values(mocks).forEach((fn) => fn.mockReset());
  saved = [];
  vi.stubGlobal("URL", { ...URL, createObjectURL: () => "blob:placeholder", revokeObjectURL: () => undefined });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    saved.push({ download: this.download, href: this.href });
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Export CSV on the Tasks screen", () => {
  it("sits beside New task, downloads the file under the name the server gave, and says so", async () => {
    mocks.exportCsv.mockResolvedValue({ blob: new Blob([csv.response.body_text ?? ""], { type: "text/csv" }), filename: NAME });
    render(<TasksView initial={board} />);
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    expect(mocks.exportCsv).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(saved).toEqual([{ download: NAME, href: "blob:placeholder" }]));
    expect(await screen.findByText(`${NAME} downloaded`)).toBeTruthy();
    expect(screen.getByRole("button", { name: "New task" })).toBeTruthy();
  });

  it("names the file itself when the server sent no name", async () => {
    mocks.exportCsv.mockResolvedValue({ blob: new Blob(["x"]), filename: null });
    render(<TasksView initial={board} />);
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(saved[0]?.download).toBe("tasks.csv"));
  });

  it("is disabled while the file is being made, so one click makes one file", async () => {
    let finish: (file: { blob: Blob; filename: string }) => void = () => undefined;
    mocks.exportCsv.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    render(<TasksView initial={board} />);
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    const busy = screen.getByRole("button", { name: "Preparing..." }) as HTMLButtonElement;
    expect(busy.disabled).toBe(true);
    finish({ blob: new Blob(["x"]), filename: NAME });
    await waitFor(() => expect((screen.getByRole("button", { name: "Export CSV" }) as HTMLButtonElement).disabled).toBe(false));
  });

  it("says something went wrong when the file could not be made (a 404 is developer text, never shown)", async () => {
    mocks.exportCsv.mockRejectedValue(new ApiError(404, "not_found", "No such team."));
    render(<TasksView initial={board} />);
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    expect(await screen.findByText("Something went wrong. Please try again.")).toBeTruthy();
    expect(saved).toEqual([]);
  });
});

describe("the client call", () => {
  const body = csv.response.body_text ?? "";
  const fileResponse = () =>
    new Response("﻿" + body, { status: 200, headers: csv.response.headers });

  it("asks for format=csv with the active team's header and returns the file and its name", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    const api = createApi({
      teamId: () => 7,
      fetch: (async (url: string, init: RequestInit) => {
        calls.push({ url, init });
        return fileResponse();
      }) as unknown as typeof fetch,
    });
    const file = await api.tasks.exportCsv();
    expect(calls[0].url).toBe("/api/tasks/export?format=csv");
    expect((calls[0].init.headers as Record<string, string>)["X-Team-Id"]).toBe("7");
    expect(file.filename).toBe(NAME);
    expect(file.blob.size).toBeGreaterThan(body.length);
  });

  it("throws the API's error for a team that is not the caller's", async () => {
    const notMine = formatsCapture("19-tasks-csv-not-my-team.json").response;
    const api = createApi({
      fetch: (async () => new Response(JSON.stringify(notMine.body), { status: notMine.status, headers: { "content-type": "application/json" } })) as unknown as typeof fetch,
    });
    await expect(api.tasks.exportCsv()).rejects.toMatchObject({ status: 404, code: "not_found" });
  });
});
