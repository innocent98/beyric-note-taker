export const SITE_NAME = "Beyric Note Taker";
export const LEGAL_NAME = "Beyrichq LTD";
// The share image (scripts/make-icons.sh). A page that sets its own openGraph replaces the layout's,
// so every page that does passes this too.
export const OG_IMAGES = [{ url: "/og.png", width: 1200, height: 630, alt: SITE_NAME }];
export const SITE_URL = process.env.SITE_URL ?? "https://notes.beyrictech.com";
export const SITE_DESCRIPTION =
  "Beyric Note Taker joins your Google Meet and Zoom calls and writes clear notes: a summary, decisions and action items.";

// Shown on /privacy. compose.yaml passes the same AUDIO_RETENTION_DAYS the app uses (default 7,
// app/config.py), so the policy cannot drift from the code that deletes the audio.
export function audioRetentionDays(): number {
  const raw = Number.parseInt(process.env.AUDIO_RETENTION_DAYS ?? "", 10);
  return Number.isFinite(raw) && raw > 0 ? raw : 7;
}
