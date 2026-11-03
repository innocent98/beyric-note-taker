import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PrivacyContent } from "@/components/legal/privacy-content";
import { TermsContent } from "@/components/legal/terms-content";
import { join } from "node:path";
import { read, REPO } from "./helpers";

// Google compares the privacy policy with the app, and the legal entity must be right, so the
// text of the Next pages must equal the Jinja templates they replace (app/templates/*.html).

const ENTITIES: Record<string, string> = {
  "&middot;": "·",
  "&copy;": "©",
  "&amp;": "&",
  "&quot;": '"',
  "&#x27;": "'",
  "&#39;": "'",
  "&lt;": "<",
  "&gt;": ">",
  "&nbsp;": " ",
};

function text(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&[#\w]+;/g, (e) => ENTITIES[e] ?? e)
    .replace(/\s+/g, " ")
    .trim();
}

function jinjaMain(name: string): string {
  const source = read(join(REPO, "app/templates", name));
  const body = /<main class="pub-main legal">([\s\S]*)<\/main>/.exec(source)?.[1];
  if (!body) throw new Error(`no legal <main> in ${name}`);
  return body.replace("{{ audio_retention_days }}", "7");
}

const attrs = (html: string, name: string) =>
  [...html.matchAll(new RegExp(`\\b${name}="([^"]*)"`, "g"))].map((m) => m[1].replaceAll("&amp;", "&"));

describe.each([
  ["privacy.html", () => renderToStaticMarkup(<PrivacyContent days={7} />)],
  ["terms.html", () => renderToStaticMarkup(<TermsContent />)],
])("%s", (name, render) => {
  const jinja = jinjaMain(name);
  const react = render();

  it("has the same text as the Jinja template", () => {
    expect(text(react)).toBe(text(jinja));
  });

  it("keeps every anchor id (such as /privacy#google) and every link", () => {
    expect(attrs(react, "id")).toEqual(attrs(jinja, "id"));
    expect(attrs(react, "href")).toEqual(attrs(jinja, "href"));
  });

  it("names the legal entity and has no placeholders", () => {
    expect(text(react)).toContain("Beyrichq LTD");
    expect(text(react)).toContain("Lagos, Nigeria");
    expect(text(react)).not.toMatch(/\[[^\]]*\]/);
  });
});

describe("privacy retention", () => {
  it("shows the number of days it is given", () => {
    expect(text(renderToStaticMarkup(<PrivacyContent days={3} />))).toContain(
      "Meeting audio: deleted automatically 3 days after the meeting.",
    );
  });
});
