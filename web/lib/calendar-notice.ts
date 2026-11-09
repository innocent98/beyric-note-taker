// A plain module on purpose: the server page reads the query here, the client component shows it.

// What the callback's redirect put in the query string (docs/fe-integration-guide-calendar-connect.md
// section 2): `?calendar=connected` or `?calendar_error=<code>`, never both.
export type CalendarNotice = { kind: "connected" } | { kind: "error"; code: string };

const ERROR_CODES = [
  "google_denied",
  "scope_missing",
  "no_refresh_token",
  "google_failed",
  "different_account",
  "wrong_account",
  // Outlook (docs/fe-integration-guide-calendar-outlook.md): its own refusals, and the one a second
  // connect gets while another calendar is connected.
  "microsoft_denied",
  "microsoft_failed",
  "no_mailbox",
  "tenant_not_allowed",
  "already_connected",
] as const;

const first = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value);

// Reads the redirect's query. A code this page does not know becomes `google_failed`, so nothing
// from the URL is ever printed.
export function calendarNotice(params: Record<string, string | string[] | undefined>): CalendarNotice | null {
  const error = first(params.calendar_error);
  if (error !== undefined) {
    return { kind: "error", code: (ERROR_CODES as readonly string[]).includes(error) ? error : "google_failed" };
  }
  return first(params.calendar) === "connected" ? { kind: "connected" } : null;
}
