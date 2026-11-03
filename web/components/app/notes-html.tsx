// The one place server-rendered HTML is put into a page. `notes_html` is the API's own markdown
// rendering of the notes: raw HTML escaped, javascript: links dropped, links given
// rel="noopener noreferrer nofollow", images turned into links (docs/fe-integration-guide-owner-app.md
// section 5.2, checked by tests/test_api.py on the server). Do not render `notes` with another
// markdown library, and do not use dangerouslySetInnerHTML for anything else (tests/guards.test.ts).
export function NotesHtml({ html }: { html: string }) {
  return <div className="notes-body notes" dangerouslySetInnerHTML={{ __html: html }} />;
}
