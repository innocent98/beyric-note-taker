import { Alert } from "@/components/ui";
import { initials } from "@/lib/format";
import { parseTranscript } from "@/lib/transcript";

const TONES = ["avatar-a", "avatar-b", "avatar-c", "avatar-d"] as const;

// The transcript as the designer draws it: one row per turn with the speaker's initials. Text with
// no `[Name]:` labels (no speaker names yet) is shown as it is.
export function TranscriptPanel({ text, incomplete }: { text: string; incomplete: boolean }) {
  const lines = parseTranscript(text);
  const speakers = [...new Set(lines.map((l) => l.speaker).filter((s): s is string => s !== null))];
  return (
    <div className="transcript-body">
      {incomplete ? <Alert kind="notice">Speaker names unavailable for part of this transcript.</Alert> : null}
      {lines.map((line, index) => (
        <div className="transcript-line" key={index}>
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
            <>
              <span />
              <p className="transcript-text">{line.text}</p>
            </>
          )}
        </div>
      ))}
    </div>
  );
}
