import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { GET, POST } from "@/app/share-target/route";
import manifest from "@/app/manifest";
import { WEB } from "./helpers";

function share(fields: Record<string, string>, enctype: "multipart" | "urlencoded" = "multipart") {
  if (enctype === "multipart") {
    const form = new FormData();
    for (const [key, value] of Object.entries(fields)) form.set(key, value);
    return new Request("http://localhost/share-target", { method: "POST", body: form });
  }
  return new Request("http://localhost/share-target", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(fields),
  });
}

describe("POST /share-target", () => {
  it("finds the link in the shared text and sends only that link after the #", async () => {
    const response = await POST(
      share({ title: "Weekly sync", text: "Join us: https://meet.google.com/abc-defg-hij. Thanks!" }),
    );
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/share#link=https%3A%2F%2Fmeet.google.com%2Fabc-defg-hij&others=0");
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.text()).toBe("");
  });

  it("works for a urlencoded form too, keeps a Zoom passcode and counts other links", async () => {
    const response = await POST(
      share(
        {
          text: "https://us02web.zoom.us/j/81234567890?pwd=AbC123&x=1 or https://meet.google.com/abc-defg-hij",
        },
        "urlencoded",
      ),
    );
    expect(response.headers.get("location")).toBe(
      "/share#link=https%3A%2F%2Fus02web.zoom.us%2Fj%2F81234567890%3Fpwd%3DAbC123&others=1",
    );
  });

  it("never puts the shared text itself in the redirect", async () => {
    const response = await POST(
      share({ text: "Private agenda: layoffs on Friday. https://meet.google.com/abc-defg-hij", title: "Secret title" }),
    );
    const location = response.headers.get("location") ?? "";
    expect(decodeURIComponent(location)).not.toMatch(/layoffs|agenda|Secret/);
  });

  it.each([
    ["no link", { text: "lunch?" }],
    ["a hostile look-alike", { text: "https://meet.google.com@evil.example/abc-defg-hij" }],
    ["nothing at all", {}],
  ])("says there was nothing to send for %s", async (_name, fields) => {
    const response = await POST(share(fields));
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/share#none");
  });

  it("treats a body that is not a form as an empty share", async () => {
    const response = await POST(new Request("http://localhost/share-target", { method: "POST", body: "{not a form" }));
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/share#none");
  });

  it("a GET to it only goes on to /share", () => {
    const response = GET();
    expect(response.status).toBe(303);
    expect(response.headers.get("location")).toBe("/share#none");
  });
});

describe("manifest and service worker", () => {
  it("registers a POST share target that sends title, text and url to /share-target", () => {
    const m = manifest();
    expect(m.share_target).toEqual({
      action: "/share-target",
      method: "POST",
      enctype: "multipart/form-data",
      params: { title: "title", text: "text", url: "url" },
    });
    // Everything Chrome asks of an installable manifest.
    expect(m.start_url).toBe("/");
    expect(m.display).toBe("standalone");
    expect(m.name).toBeTruthy();
    expect(m.icons?.map((i) => i.sizes)).toEqual(expect.arrayContaining(["192x192", "512x512"]));
    expect(m.scope && m.share_target?.action.startsWith(m.scope)).toBe(true);
  });

  it("has a fetch handler that is network-only: it stores nothing", () => {
    const source = readFileSync(join(WEB, "public/sw.js"), "utf8").replace(/^\s*\/\/.*$/gm, "");
    expect(source).toMatch(/addEventListener\("fetch"/);
    expect(source).toMatch(/fetch\(request\)/);
    // No Cache API, no storage, no background sync: pages and /api answers are private.
    expect(source).not.toMatch(/\bcaches\b|cache\.put|cache\.add|indexedDB|localStorage|sessionStorage/);
    // Only page navigations are handled; a POST (the share itself) and API calls are left alone.
    expect(source).toMatch(/request\.mode !== "navigate" \|\| request\.method !== "GET"\) return/);
  });
});
