import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { metadata } from "@/app/layout";
import { wrapSections } from "@/components/app/notes-html";
import { Landing, LandingFooter, LandingHeader } from "@/components/landing/landing";
import { PrivacyContent } from "@/components/legal/privacy-content";
import { TermsContent } from "@/components/legal/terms-content";
import { PRIVACY_TOC, TERMS_TOC } from "@/components/legal/toc";
import { Logo } from "@/components/ui";

// Checks that keep the pages on the designer's structure (docs/ui/parity-report.md).

describe("site icons", () => {
  it("declares the favicon and the Apple icon in the layout metadata", () => {
    const icons = metadata.icons as { icon: { url: string }[]; apple: string };
    expect(icons.icon.map((i) => i.url)).toContain("/icon.svg");
    expect(icons.apple).toBe("/icons/apple-touch-icon.png");
  });
});

describe("notes sections", () => {
  const html = "<h2>Summary</h2>\n<p>Hello</p>\n<h2>Decisions</h2>\n<ul><li>Ship</li></ul>\n<h2>Open questions</h2>\n<p>None noted</p>\n<h2>Please check</h2>\n<p>None noted</p>\n";

  it("wraps each part under its heading in a section the stylesheet knows", () => {
    const out = wrapSections(html);
    expect(out).toContain('<section class="notes-sec notes-sec-summary"><h2>Summary</h2>');
    expect(out).toContain('<section class="notes-sec notes-sec-decisions"><h2>Decisions</h2>');
    expect(out).toContain('<section class="notes-sec notes-sec-questions"><h2>Open questions</h2>');
    expect(out).toContain('<section class="notes-sec notes-sec-check"><h2>Please check</h2>');
    expect(out.match(/<section/g)).toHaveLength(4);
    expect(out.match(/<\/section>/g)).toHaveLength(4);
  });

  it("keeps every word of the notes and puts nothing from them into an attribute", () => {
    const hostile = '<h2>"><script>x</script></h2><p>body</p>';
    const out = wrapSections(hostile);
    expect(out).toContain('<section class="notes-sec ">');
    expect(out.replace(/<section class="notes-sec ">|<\/section>/g, "")).toBe(hostile);
  });

  it("draws the translated headings (the fixed dictionary) the same way as the English ones", () => {
    const kinds = {
      fr: ["Résumé", "Points clés", "Décisions", "Actions à mener", "Questions ouvertes", "À vérifier"],
      es: ["Resumen", "Puntos clave", "Decisiones", "Tareas", "Preguntas abiertas", "Revisar"],
    };
    const classes = ["summary", "key", "decisions", "actions", "questions", "check"];
    for (const names of Object.values(kinds)) {
      names.forEach((name, i) => {
        expect(wrapSections(`<h2>${name}</h2><p>x</p>`)).toContain(`<section class="notes-sec notes-sec-${classes[i]}">`);
      });
    }
  });

  it("gives a heading named like an Object property no class", () => {
    for (const name of ["constructor", "__proto__", "toString", "hasOwnProperty"]) {
      expect(wrapSections(`<h2>${name}</h2><p>x</p>`)).toBe(`<section class="notes-sec "><h2>${name}</h2><p>x</p></section>`);
    }
  });

  it("leaves text before the first heading, and notes with no heading, as they are", () => {
    expect(wrapSections("<p>Just text</p>")).toBe("<p>Just text</p>");
    expect(wrapSections("<p>Intro</p><h2>Summary</h2><p>x</p>")).toBe(
      '<p>Intro</p><section class="notes-sec notes-sec-summary"><h2>Summary</h2><p>x</p></section>',
    );
  });
});

describe("legal pages", () => {
  const headings = (html: string) => [...html.matchAll(/<h2 id="([^"]+)">([^<]*)<\/h2>/g)].map((m) => [m[1], m[2]]);

  it("list every heading in 'On this page', in order", () => {
    expect(headings(renderToStaticMarkup(<PrivacyContent days={7} />))).toEqual(PRIVACY_TOC.map((t) => [...t]));
    expect(headings(renderToStaticMarkup(<TermsContent />))).toEqual(TERMS_TOC.map((t) => [...t]));
  });
});

describe("logo", () => {
  it("draws the b mark from Brand.dc.html, with the centre dot", () => {
    const html = renderToStaticMarkup(<Logo href={null} />);
    expect(html).toContain('viewBox="0 0 120 120"');
    expect(html).toContain('d="M38 24V72A24 24 0 1 0 62 48"');
    expect(html).toContain('<circle cx="62" cy="72" r="6"');
    expect(html).toContain("Beyric");
    expect(html).toContain("Note Taker");
  });
});

describe("landing page", () => {
  const html = renderToStaticMarkup(
    <>
      <LandingHeader />
      <Landing />
      <LandingFooter />
    </>,
  );

  it("has the designer's sections, the anchors in its header, and five questions", () => {
    for (const id of ["workflow", "features", "security", "faq"]) {
      expect(html).toContain(`id="${id}"`);
      expect(html).toContain(`href="#${id}"`);
    }
    expect(html.match(/<details/g)).toHaveLength(5);
    expect(html).toContain("Your meetings, turned into finished work.");
  });

  it("links only to places that exist: no sign-up and no prototype file", () => {
    const hrefs = [...html.matchAll(/href="([^"]+)"/g)].map((m) => m[1]);
    expect(hrefs.filter((h) => !h.startsWith("#"))).toEqual(
      expect.arrayContaining(["/login", "/privacy", "/terms", "mailto:privacy@beyrictech.com"]),
    );
    expect(hrefs.some((h) => /signup|sign-up|\.dc\.html|register/i.test(h))).toBe(false);
  });
});
