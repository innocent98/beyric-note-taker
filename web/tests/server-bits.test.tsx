import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TranscriptPanel } from "@/components/app/transcript-panel";
import { AppShell } from "@/components/app/shell";
import { NotesHtml } from "@/components/app/notes-html";
import { formatTime, initials, plural } from "@/lib/format";
import { parseTranscript } from "@/lib/transcript";
import { captured } from "./helpers";
import type { Meeting } from "@/lib/api/types";

const hdrs = vi.hoisted(() => ({ cookie: "session=old" }));
vi.mock("next/headers", () => ({
  headers: async () => new Headers({ cookie: hdrs.cookie, "x-forwarded-for": "203.0.113.9" }),
}));

describe("transcript", () => {
  const text = captured<Meeting>("47-meeting-detail-ready.json").transcript!;

  it("splits the captured speaker-labelled transcript into turns", () => {
    expect(parseTranscript(text)).toEqual([
      { speaker: "Ada Example", text: "Hello everyone, thanks for joining. I can ship the beta on Friday." },
      { speaker: "Ben Sample", text: "Good. I will take the support rota from Monday." },
    ]);
  });

  it("keeps plain text as one block and continuation lines with their turn", () => {
    expect(parseTranscript("just words\nmore words")).toEqual([{ speaker: null, text: "just words\nmore words" }]);
    expect(parseTranscript("[A]: one\ncontinues\n[B]: two")).toEqual([
      { speaker: "A", text: "one\ncontinues" },
      { speaker: "B", text: "two" },
    ]);
  });

  it("renders initials per speaker", () => {
    const html = renderToStaticMarkup(<TranscriptPanel text={text} incomplete={false} />);
    expect(html).toContain(">AE<");
    expect(html).toContain(">BS<");
    expect(html).not.toContain("Speaker names unavailable");
  });
});

describe("formatting", () => {
  it("formats times in UTC with the words of the Jinja pages", () => {
    expect(formatTime("2026-11-02T21:16:52.726490Z")).toBe("02 Nov 2026, 21:16 UTC");
    expect(formatTime("not a date")).toBe("not a date");
  });
  it("pluralises and makes initials", () => {
    expect(plural(1, "meeting")).toBe("1 meeting");
    expect(plural(2, "term")).toBe("2 terms");
    expect(initials("Ada Example")).toBe("AE");
    expect(initials("ben")).toBe("B");
  });
});

describe("notes", () => {
  it("puts the server's HTML in unchanged", () => {
    const m = captured<Meeting>("47-meeting-detail-ready.json");
    const html = renderToStaticMarkup(<NotesHtml html={m.notes_html!} />);
    expect(html).toContain("<h2>Summary</h2>");
    expect(html).toContain("<strong>beta</strong>");
  });
});

describe("shell", () => {
  it("marks the current section and posts log out to /logout", () => {
    const html = renderToStaticMarkup(<AppShell current="templates">x</AppShell>);
    expect(html).toMatch(/href="\/templates"[^>]*aria-current="page"|aria-current="page"[^>]*href="\/templates"/);
    expect(html).not.toMatch(/href="\/"[^>]*aria-current/);
    expect(html).toContain('action="/logout"');
    expect(html).toContain('method="post"');
    expect(html).toContain('aria-label="Log out"');
  });
});

describe("POST /logout", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  function stubFetch(handler: (url: string, init: RequestInit) => Response) {
    const calls: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push({ url: String(url), init });
      return handler(String(url), init);
    });
    return calls;
  }
  const json = (body: unknown, init: ResponseInit = {}) =>
    new Response(JSON.stringify(body), { ...init, headers: { "content-type": "application/json", ...(init.headers as object) } });

  it("ends the session with the visitor's cookie and passes the new cookie on", async () => {
    const calls = stubFetch((url, init) =>
      init.method === "GET"
        ? json({ authenticated: true, csrf_token: "tok" })
        : json({ authenticated: false, csrf_token: "new" }, { headers: { "set-cookie": "session=fresh; HttpOnly; Path=/" } }),
    );
    const { POST } = await import("@/app/logout/route");
    const res = await POST();
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/login");
    expect(res.headers.get("set-cookie")).toBe("session=fresh; HttpOnly; Path=/");
    expect(calls.map((c) => c.init.method)).toEqual(["GET", "DELETE"]);
    const sent = calls[1].init.headers as Record<string, string>;
    expect(sent["X-CSRF-Token"]).toBe("tok");
    expect(sent.cookie).toBe("session=old");
    expect(sent["x-forwarded-for"]).toBe("203.0.113.9");
  });

  it("still sends the visitor to /login when the session was already gone", async () => {
    stubFetch((url, init) =>
      init.method === "GET"
        ? json({ authenticated: false, csrf_token: "t" })
        : json({ error: { code: "not_authenticated", message: "Log in first." } }, { status: 401 }),
    );
    const { POST } = await import("@/app/logout/route");
    const res = await POST();
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/login");
  });

  it("does not claim a logout that did not happen", async () => {
    stubFetch(() => new Response("bad gateway", { status: 502, headers: { "content-type": "text/html" } }));
    const { POST } = await import("@/app/logout/route");
    expect((await POST()).status).toBe(502);
  });

  it("never logs out on a GET", async () => {
    const { GET } = await import("@/app/logout/route");
    const res = GET();
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/");
  });
});
