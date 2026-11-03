export type TranscriptLine = { speaker: string | null; text: string };

// The API's `transcript` is one string: the speaker-labelled version has one `[Name]: words` line
// per turn, the plain one has no labels (docs/fe-integration-guide-owner-app.md section 6). This
// is display only: the Copy button copies the string as it came. A line without a label continues
// the turn above it; if no line has a label the whole text is one unlabelled block.
const LABELLED = /^\[([^\]\n]+)\]:\s?(.*)$/;

export function parseTranscript(text: string): TranscriptLine[] {
  const lines: TranscriptLine[] = [];
  for (const raw of text.split("\n")) {
    const match = LABELLED.exec(raw);
    if (match) {
      lines.push({ speaker: match[1], text: match[2] });
    } else if (lines.length > 0) {
      lines[lines.length - 1].text += `\n${raw}`;
    } else {
      lines.push({ speaker: null, text: raw });
    }
  }
  return lines.some((line) => line.speaker !== null)
    ? lines.map((line) => ({ ...line, text: line.text.trimEnd() }))
    : [{ speaker: null, text }];
}
