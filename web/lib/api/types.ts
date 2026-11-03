import type { components } from "./schema";

// Names for the shapes in schema.d.ts, which `npm run gen:api` generates from docs/openapi.json.
// Do not write a payload type by hand: add a name here for one the schema already has.
type Schemas = components["schemas"];

export type SessionState = Schemas["SessionState"];
export type MeetingSummary = Schemas["MeetingSummary"];
export type MeetingList = Schemas["MeetingList"];
export type Meeting = Schemas["MeetingDetail"];
export type NewMeeting = Schemas["NewMeeting"];
export type Template = Schemas["TemplateOut"];
export type TemplateInput = Schemas["TemplateIn"];
export type Term = Schemas["TermOut"];

export type MeetingStatus = Meeting["status"];
export type StatusTone = Meeting["status_tone"];
export type RetryBlock = NonNullable<Meeting["retry_block"]>;

export type FieldError = Schemas["FieldError"];
