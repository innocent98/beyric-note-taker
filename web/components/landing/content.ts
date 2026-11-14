// The words and icon paths of the landing page, as drawn in the designer's Landing.dc.html.
// Copy is the designer's. Where a line describes a feature the app does not have yet, it is listed
// in docs/ui/parity-report.md ("Copy that runs ahead of the product") so the owner can decide.
// Exception, 2026-11-14: the first security card says only what is true (TLS in transit, calendar
// tokens stored encrypted). No disk or database encryption is set up, so no line may claim
// encryption of recordings or notes at rest, or "end to end" (docs/sop/2026-11-14-landing-security-copy.md).

export const FEATURES: ReadonlyArray<{ title: string; body: string; icon: string }> = [
  { title: "Tasks, sprints and backlogs", body: "Action items land on a board, grouped by project and sprint. Unfinished work rolls into the next sprint on its own.", icon: "M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" },
  { title: "Recurring meetings that remember", body: "Each Monday planning opens with what is still unfinished and the questions left open last time.", icon: "M4 12a8 8 0 0 1 14-5.3L20 9M20 4v5h-5M20 12a8 8 0 0 1-14 5.3L4 15M4 20v-5h5" },
  { title: "A searchable decisions log", body: "Every decision from every meeting in one list, linked back to the notes where it was made.", icon: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4" },
  { title: "Templates and vocabulary", body: "Tell the notes what to focus on for client calls, interviews or updates, and spell names the way your team does.", icon: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h4" },
  { title: "Share, export and translate", body: "Email notes to attendees, download them as PDF or Word, or read them in another language.", icon: "M22 2 11 13M22 2 15 22l-4-9-9-4 20-7z" },
  { title: "Reminders before every call", body: "Attendees get a short brief before recurring meetings: last time, decisions, open questions and tasks.", icon: "M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0" },
];

export const SECURITY: ReadonlyArray<{ title: string; body: string; icon: string }> = [
  { title: "Encrypted in transit", body: "Traffic to the site is encrypted with TLS, and the sign-in tokens for connected calendars are stored encrypted.", icon: "M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4" },
  { title: "Never used for training", body: "Your audio, transcripts and notes are not used to train any processing service, ours or anyone else’s.", icon: "M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z" },
  { title: "Visible to everyone", body: "The note taker appears in the participant list under a clear name while it records, and anyone can ask the host to remove it.", icon: "M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" },
  { title: "You control retention", body: "Delete a meeting, export your notes or close your account at any time. Audio is removed automatically after a set period.", icon: "M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" },
];

export const FAQS: ReadonlyArray<{ q: string; a: string }> = [
  { q: "Which meeting platforms does it work with?", a: "Google Meet, Microsoft Teams and Zoom. Paste a meeting link, or connect Google Calendar or Outlook and the note taker joins upcoming meetings that have a video link." },
  { q: "Do participants know they are being recorded?", a: "Yes. The note taker joins as a visible participant with a clear name. You are responsible for telling attendees and getting any consent your local law requires." },
  { q: "How are tasks assigned to the right person?", a: "Each action item is matched to a teammate by name, including the names they appear as in meetings. Items with no clear owner wait under Needs owner so someone can assign them." },
  { q: "Can we keep using Jira, Linear or Asana?", a: "Yes. Tasks, projects and sprints can be sent to the tool your team already uses, so the notes feed your existing workflow." },
  { q: "Can I download or share the notes?", a: "You can email notes to attendees or anyone else, attach a PDF or Word copy, and download either format with or without the full transcript." },
];

export const TOOLS = ["Google Calendar", "Outlook", "Slack", "Jira", "Linear", "Asana"] as const;
export const PLATFORMS = ["Google Meet", "Microsoft Teams", "Zoom"] as const;
