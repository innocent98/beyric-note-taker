// The one place server-rendered HTML is put into a page. `notes_html` is the API's own markdown
// rendering of the notes: raw HTML escaped, javascript: links dropped, links given
// rel="noopener noreferrer nofollow", images turned into links (docs/fe-integration-guide-owner-app.md
// section 5.2, checked by tests/test_api.py on the server). Do not render `notes` with another
// markdown library, and do not use dangerouslySetInnerHTML for anything else (tests/guards.test.ts).

// The designer draws each part of the notes its own way: check marks for decisions, quiet dots for
// open questions, a grey box for "Please check". The API sends one flat HTML string, so each part
// that starts with an <h2> is wrapped in a <section> whose class comes from a fixed list; nothing
// from the notes ends up in an attribute.
const SECTION_CLASS: Record<string, string> = {
  summary: "notes-sec-summary",
  "key points": "notes-sec-key",
  decisions: "notes-sec-decisions",
  "action items": "notes-sec-actions",
  "open questions": "notes-sec-questions",
  "please check": "notes-sec-check",
};
// The same six parts under the headings a translation uses (the fixed dictionary in
// app/translation.py, the designer's own words), so a translated page is drawn the same way.
for (const [kind, names] of Object.entries({
  "notes-sec-summary": ["résumé", "resumen"],
  "notes-sec-key": ["points clés", "puntos clave"],
  "notes-sec-decisions": ["décisions", "decisiones"],
  "notes-sec-actions": ["actions à mener", "tareas"],
  "notes-sec-questions": ["questions ouvertes", "preguntas abiertas"],
  "notes-sec-check": ["à vérifier", "revisar"],
})) {
  for (const name of names) SECTION_CLASS[name] = kind;
}

export function wrapSections(html: string): string {
  return html
    .split(/(?=<h2[ >])/)
    .map((part) => {
      const heading = /^<h2[^>]*>([\s\S]*?)<\/h2>/.exec(part)?.[1];
      if (heading === undefined) return part;
      const name = heading.replace(/<[^>]*>/g, "").trim().toLowerCase();
      // hasOwn, so a heading such as "constructor" or "__proto__" finds nothing instead of a
      // property of Object.prototype.
      const known = Object.hasOwn(SECTION_CLASS, name) ? SECTION_CLASS[name] : "";
      return `<section class="notes-sec ${known}">${part}</section>`;
    })
    .join("");
}

export function NotesHtml({ html }: { html: string }) {
  return <div className="notes-body notes" dangerouslySetInnerHTML={{ __html: wrapSections(html) }} />;
}
