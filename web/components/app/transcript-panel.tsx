import { Alert } from "@/components/ui";
import { initials } from "@/lib/format";
import { parseTranscript } from "@/lib/transcript";

const TONES = ["avatar-a", "avatar-b", "avatar-c", "avatar-d"] as const;

// The transcript as the designer draws it: one row per turn with the speaker's initials. Text with
// no `[Name]:` labels (no speaker names yet) is shown as it is. Each row has the id `tl-N` (N from 1,
// the row's place in parseTranscript) so a link can scroll to it, and `highlight` gives row N the
// designer's pale yellow (D:287). A number outside the rows highlights nothing. No time is shown
// (none is stored).
export function TranscriptPanel({ text, incomplete, highlight = null }: { text: string; incomplete: boolean; highlight?: number | null }) {
  const lines = parseTranscript(text);
  const speakers = [...new Set(lines.map((l) => l.speaker).filter((s): s is string => s !== null))];
  return (
    <div className="transcript-body">
      {incomplete ? <Alert kind="notice">Speaker names unavailable for part of this transcript.</Alert> : null}
      {lines.map((line, index) => (
        <div
          id={`tl-${index + 1}`}
          className={[
            "transcript-line",
            line.speaker === null ? "transcript-line-plain" : "",
            highlight === index + 1 ? "transcript-line-hl" : "",
          ]
            .filter(Boolean)
            .join(" ")}
          key={index}
        >
          {line.speaker !== null ? (
            <>
              <div className={`avatar avatar-sm ${TONES[speakers.indexOf(line.speaker) % TONES.length]}`} aria-hidden="true">
                {initials(line.speaker)}
              </div>
              <div>
                <p className="transcript-who">{line.speaker}</p>
                <p className="transcript-text">{line.text}</p>
              </div>
            </>
          ) : (
            <p className="transcript-text">{line.text}</p>
          )}
        </div>
      ))}
    </div>
  );
}
