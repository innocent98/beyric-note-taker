import type { components, operations } from "./schema";

// Names for the shapes in schema.d.ts, which `npm run gen:api` generates from docs/openapi.json.
// Do not write a payload type by hand: add a name here for one the schema already has.
type Schemas = components["schemas"];

export type SessionState = Schemas["SessionState"];
export type SessionUser = Schemas["UserOut"];
export type Usage = Schemas["UsageOut"];
export type MeetingSummary = Schemas["MeetingSummary"];
export type MeetingList = Schemas["MeetingList"];
export type Meeting = Schemas["MeetingDetail"];
export type NewMeeting = Schemas["NewMeeting"];
export type Template = Schemas["TemplateOut"];
export type TemplateInput = Schemas["TemplateIn"];
export type Term = Schemas["TermOut"];
export type MeetingRef = Schemas["MeetingRef"];
export type Decision = Schemas["DecisionOut"];
export type DecisionGroup = Schemas["DecisionGroup"];
export type DecisionList = Schemas["DecisionList"];
export type DecisionStatus = Decision["status"];
export type DecisionRef = Schemas["DecisionRef"];
export type DecisionTaskRef = Schemas["DecisionTaskRef"];
export type DecisionCounts = Schemas["DecisionCounts"];
// A change sends only the fields that change; the schema lists them all as optional already.
export type DecisionChanges = Partial<Schemas["DecisionPatch"]>;
export type ReviewItem = Schemas["ReviewItemOut"];
export type ReviewList = Schemas["ReviewList"];
export type ReviewTermResult = Schemas["ReviewTermOut"];
export type ReviewState = "open" | "checked";
export type ExtractionStatus = NonNullable<Meeting["extraction_status"]>;

export type Task = Schemas["TaskOut"];
export type TaskList = Schemas["TaskList"];
export type TaskStatus = Task["status"];
export type TaskPerson = Schemas["PersonOut"];
export type MeetingTasks = Schemas["MeetingTasks"];
export type TasksSettings = Schemas["TasksSettingsOut"];
export type SlackState = Schemas["SlackOut"];
export type LinearState = Schemas["LinearOut"];
export type LinearTeam = Schemas["LinearTeamOut"];
// A change sends only the fields that change; the schema lists the defaulted ones as required.
export type TaskChanges = Partial<Schemas["TaskPatch"]>;
export type NewTask = Schemas["TaskCreate"];

export type Project = Schemas["ProjectOut"];
export type ProjectList = Schemas["ProjectList"];
export type ProjectChanges = Partial<Schemas["ProjectPatch"]>;
export type Milestone = Schemas["MilestoneOut"];
export type MilestoneList = Schemas["MilestoneList"];
export type MeetingMilestones = Schemas["MeetingMilestones"];
export type Share = Schemas["ShareOut"];
export type ShareList = Schemas["ShareList"];
export type ShareCreated = Schemas["ShareCreated"];
export type ShareBody = Schemas["ShareIn"];
export type ShareRecipient = Schemas["ShareRecipientOut"];

export type Sprint = Schemas["SprintOut"];
export type SprintList = Schemas["SprintList"];
export type SprintClosed = Schemas["SprintClosed"];
export type SprintLength = SprintList["sprint_length"];

export type Team = Schemas["TeamOut"];
export type TeamRole = Team["my_role"];
export type TeamMember = Schemas["TeamMemberOut"];
export type TeamInvite = Schemas["TeamInviteOut"];
export type TeamMembers = Schemas["TeamMembers"];
export type TeamInviteResult = Schemas["InviteResult"];
export type TeamInvitesCreated = Schemas["InvitesCreated"];
export type InvitePreview = Schemas["InvitePreview"];
export type InviteRole = Schemas["InvitesIn"]["role"];
export type TeamChanges = Partial<Schemas["TeamPatch"]>;
export type TeamMemberChanges = Partial<Schemas["TeamMemberPatch"]>;

export type MeetingStatus = Meeting["status"];
export type StatusTone = Meeting["status_tone"];
export type RetryBlock = NonNullable<Meeting["retry_block"]>;

export type FieldError = Schemas["FieldError"];
export type EmailPreferences = Schemas["EmailPreferencesOut"];
export type LanguagePreferences = Schemas["LanguagePreferencesOut"];
export type TimeZonePreferences = Schemas["TimeZoneOut"];
export type Translation = Schemas["TranslationOut"];
export type TranslationBrief = Schemas["TranslationBrief"];
// `en` is the original; only `fr` and `es` are translated into.
export type NoteLanguage = LanguagePreferences["default_language"];
export type TranslationLanguage = Translation["language"];
export type Notice = Schemas["NotificationOut"];
export type NoticeList = Schemas["NotificationList"];

export type ExportFormat = NonNullable<NonNullable<operations["export_meeting"]["parameters"]["query"]>["format"]>;
export type Pilot = Schemas["PilotOut"];
export type PilotState = Pilot["state"];
export type SignUpBody = Schemas["SignUp"];

export type CalendarState = Schemas["CalendarOut"];
export type CalendarEvent = Schemas["UpcomingEvent"];
export type CalendarSettingsInput = Schemas["SettingsIn"];
export type AutoJoinRule = CalendarState["rule"];
export type ConnectStart = Schemas["ConnectOut"];
export type DisconnectResult = Schemas["DisconnectOut"];
export type RecurringState = Schemas["RecurringOut"];
export type RecurringSeries = Schemas["RecurringSeries"];

// Series (docs/fe-integration-guide-series.md).
export type SeriesCard = Schemas["SeriesCard"];
export type SeriesList = Schemas["SeriesList"];
export type SeriesPage = Schemas["SeriesPage"];
export type SeriesRef = Schemas["SeriesRef"];
export type OpenQuestion = Schemas["QuestionOut"];
export type CarriedQuestion = Schemas["CarriedQuestion"];
export type AgendaItem = Schemas["AgendaItemOut"];
export type AgendaSent = Schemas["AgendaSent"];
export type QuestionChanges = Partial<Schemas["QuestionPatch"]>;
export type MeetingSeriesResult = Schemas["MeetingSeriesOut"];
export type ReminderPreferences = Schemas["ReminderPreferencesOut"];

// Ask Beyric (docs/fe-integration-guide-ask-beyric.md): `GET /api/ask/scope` and `POST /api/ask`.
export type AskCite = Schemas["CiteOut"];
export type AskPoint = Schemas["PointOut"];
export type AskAnswer = Schemas["AskOut"];
export type AskScope = Schemas["ScopeOut"];
