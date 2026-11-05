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
export type ReviewItem = Schemas["ReviewItemOut"];
export type ReviewList = Schemas["ReviewList"];
export type ReviewTermResult = Schemas["ReviewTermOut"];
export type ReviewState = "open" | "checked";
export type ExtractionStatus = NonNullable<Meeting["extraction_status"]>;

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
