// The words of the landing page, as drawn in the designer's second Landing file (2026-11-14,
// `docs/ui/Beyric Note Taker redesign/Beyric Note Taker redesign – Landing.html`), with every claim
// cut to what the app does today. The audit is docs/superpowers/specs/2026-11-14-public-pages-audit.md;
// the list of every place these words differ from the design is in docs/sop/2026-11-14-landing-v2-and-pricing.md.
// Rules that bind this file (Adebayo, 2026-11-14):
//  * nothing that is not built: no CRM or HubSpot, no Jira or Asana, no times in the transcript, no
//    "unlimited", no brief "to every attendee", no "Hiring teams" card;
//  * no claim of encryption of recordings or notes at rest, none of end-to-end encryption, and training is
//    worded as the Privacy Policy words it (what we do, and what our providers say);
//  * no "free" wording, and sign-up is invite-only for now, said plainly (SIGNUP_NOTE below).

export const PLATFORMS = ["Google Meet", "Microsoft Teams", "Zoom"] as const;

// What Beyric connects to. Outlook is built but is new and still being tested (the FAQ and the
// "Joins on its own" card say so); Slack and Linear are one-way (task updates out, nothing back).
export const TOOLS_LABEL = "Works with";
export const TOOLS = ["Google Calendar", "Outlook calendar", "Slack", "Linear"] as const;

// Sign-up is by invitation for now. One place, so the day sign-up opens it is one edit (the
// sign-up page itself already shows the right screen: the form, or the waiting list).
export const SIGNUP_NOTE = {
  before: "Sign-up is invite-only for now. If you have an invitation, create your account; if not, ",
  link: "join the waiting list",
  after: ".",
} as const;

export const HERO = {
  pill: "The note taker that follows through",
  title: "Your meetings, turned into finished work.",
  lede: "Beyric writes the notes, matches each action item to the person who owns it, files it in the right project and sprint, and brings what is still unfinished back to your next recurring meeting.",
  sample: "A sample meeting, for illustration.",
} as const;

export const WHY = {
  eyebrow: "WHY BEYRIC",
  title: "A summary is only the start. The work is what happens next.",
  body: "Notes nobody acts on are just a nicer recording. Beyric carries each meeting through to the point where each task has an owner, or is clearly waiting for one.",
  withoutTitle: "NOTES ON THEIR OWN",
  without: [
    "A recording and a summary",
    "A list of action items",
    "Tasks copied into your tracker by hand",
    "Each meeting starts from zero",
  ],
  withTitle: "BEYRIC NOTE TAKER",
  with: [
    "Writes notes, decisions and open questions",
    "Matches each task to its owner, with a due date when one was said",
    "Files work into projects, sprints and milestones",
    "Shows what is still unfinished at the next recurring meeting",
  ],
} as const;

// The five deep dives. The mock-ups beside them are in landing.tsx (one per `id`).
export const DEEP_DIVES: ReadonlyArray<{
  id: "ask" | "heard" | "decisions" | "recurring" | "tasks";
  eyebrow: string;
  title: string;
  body: string;
  points: ReadonlyArray<string>;
}> = [
  {
    id: "ask",
    eyebrow: "ASK YOUR MEETINGS",
    title: "Ask what was decided. Get the answer and the line it came from.",
    body: "Ask in plain words across the meetings you can open. Each answer links to the exact line in the transcript, so you can check it in one click.",
    points: ["\"What did I commit to last week?\"", "\"When is the launch?\"", "\"What did Daniel say about testing?\""],
  },
  {
    id: "heard",
    eyebrow: "HONEST ABOUT WHAT IT HEARD",
    title: "It tells you when it is not sure.",
    body: "Firm commitments become tasks straight away. Anything vague waits as a suggestion, with a confidence level, the reason, and the line from the transcript. Words it could not hear clearly go to a review list.",
    points: ["Confidence shown on every suggested task", "Transcript evidence one click away", "Unclear names and terms flagged for review"],
  },
  {
    id: "decisions",
    eyebrow: "DECISIONS LOG",
    title: "Every decision, and what happened to it.",
    body: "Decisions are pulled out of every meeting into one searchable log. When the team changes its mind, mark the old decision as replaced and it points to the new one, so nobody works from last month's plan.",
    points: ["Approved, proposed, replaced or rejected", "Who made it and when, linked back to the meeting", "Revisit dates so decisions do not drift"],
  },
  {
    id: "recurring",
    eyebrow: "RECURRING MEETINGS",
    title: "Your weekly meeting remembers last week.",
    body: "Unfinished tasks and open questions carry forward on their own. Beyric notices topics that keep coming back without a decision and puts them on a suggested agenda. A team can also switch on a short reminder email before the call.",
    points: ["Carried-over tasks and open questions", "Topics stuck without a decision, called out", "An optional reminder email to team members who attended recently"],
  },
  {
    id: "tasks",
    eyebrow: "TASKS, SPRINTS AND MILESTONES",
    title: "The board fills itself in.",
    body: "Tasks land on a board grouped by project and sprint. Work that is not finished rolls into the next sprint. Launch dates mentioned in a meeting are suggested as milestones, for you to confirm, with the tasks that lead to them.",
    points: ["Projects, sprints and a backlog, built in", "Milestones with progress, suggested from what was said", "Tasks from team meetings can also be sent to Linear"],
  },
];

export const DEEP_DIVES_NOTE = "The meetings, people and dates in these examples are made up.";

// "Everything you expect from a note taker": five cards. The design's sixth, the HubSpot one, is cut.
export const FEATURES: ReadonlyArray<{ title: string; body: string }> = [
  { title: "Joins on its own", body: "Connect Google Calendar, or Outlook (new, and still being tested), and the note taker joins every meeting with a video link, or only the ones you choose." },
  { title: "Transcripts with speaker names", body: "A full transcript with who said what, matched to your teammates even when they join under another name. Names it is unsure of go to a review list." },
  { title: "Templates and vocabulary", body: "Client calls, interviews and team updates each get notes in the shape that suits them, with your product names and people spelled right." },
  { title: "Share, export and translate", body: "Email notes to attendees, download them as PDF or Word, or read the notes in French or Spanish." },
  { title: "Roles for your whole team", body: "Invite teammates as owners, admins or members, and send task updates to Slack." },
];

// "Who it's for": three cards. The design's "Hiring teams" card is cut, and HubSpot is taken out of the sales card.
export const TEAMS: ReadonlyArray<{ title: string; body: string; uses: string }> = [
  { title: "Product and engineering", body: "Planning and review meetings feed the sprint directly. Nobody rewrites action items into tickets.", uses: "Sprints · Milestones · Linear" },
  { title: "Sales and client work", body: "Every customer call ends with what was asked for, what you promised and by when, in notes you can send on by email.", uses: "Client call template · Share by email" },
  { title: "Founders and leadership", body: "See what was decided across your team's meetings and which topics keep coming back without an answer.", uses: "Decisions log · Ask your meetings" },
];

export const SECURITY_TITLE = "Your meetings stay yours.";
export const SECURITY: ReadonlyArray<{ title: string; body: string }> = [
  { title: "Encrypted in transit", body: "Traffic to the site is encrypted with TLS, and the sign-in tokens for connected calendars are stored encrypted." },
  { title: "Not used to train models", body: "We do not use your meeting audio, transcripts or notes to train AI models, and our providers say they do not train on it either. The Privacy Policy names them." },
  { title: "Visible to everyone", body: "The note taker appears in the participant list under a name you can see while it records, and anyone can ask the host to remove it." },
  { title: "You control retention", body: "Delete a meeting, export your notes or close your account at any time. Audio is removed automatically after a set period. Copies held by our providers follow their own schedules, listed in the Privacy Policy." },
];

export const FAQS: ReadonlyArray<{ q: string; a: string }> = [
  { q: "What does Beyric do beyond a summary?", a: "It turns action items into assigned tasks in a project and sprint, keeps a log of every decision, and brings unfinished work back at your next recurring meeting." },
  { q: "Which meeting platforms does it work with?", a: "Google Meet, Microsoft Teams and Zoom. Paste a meeting link, or connect Google Calendar or Outlook and the note taker joins upcoming meetings that have a video link. Outlook support is new and still being tested." },
  { q: "How are tasks assigned to the right person?", a: "Each action item is matched to a teammate by name, including the names they appear as in meetings. Items with no clear owner wait under Needs owner so someone can assign them." },
  { q: "What if the notes get something wrong?", a: "Anything the note taker is unsure about is marked. Vague commitments wait as suggested tasks with a confidence level, and words it could not hear clearly go to a review list for you to confirm." },
  { q: "Can we send tasks to our tracker?", a: "Linear, yes: once a team owner or admin connects it, each task from a meeting shared with the team is sent to Linear as an issue, with its title, due date, owner name and a link back to the meeting. Beyric also has its own board of projects, sprints and milestones. Jira and Asana are not supported." },
  { q: "Do participants know they are being recorded?", a: "The note taker joins as a visible participant, under a name you can see in the participant list. You are responsible for telling attendees and getting any consent your local law requires." },
];

export const CLOSING = {
  title: "Send it to your next call. See what gets done.",
  body: "Paste a meeting link and get notes, owners and tasks back.",
} as const;
