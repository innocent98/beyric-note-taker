export const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const two = (n: number) => String(n).padStart(2, "0");

// Times are shown in the viewer's own time zone. The server does not know it, so the server render
// (and the first browser render, which must match it) is in UTC, and `LocalTime` swaps in the
// browser's zone once the page is in the browser. Every formatter takes the zone to use; without
// one it is UTC, which is what the server render, the Jinja pages and the mail say.
export const SERVER_ZONE = "UTC";

// The browser's own zone ("Africa/Lagos"); UTC where the browser will not say.
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || SERVER_ZONE;
  } catch {
    return SERVER_ZONE;
  }
}

export type Zoned = { year: number; month: number; day: number; hour: number; minute: number; weekday: number; label: string };

// The wall-clock fields of an instant in `timeZone`, read as numbers (never from words, so ICU's
// spelling of a month or weekday cannot change what the page says) and the zone's short name
// ("UTC", "GMT+1", "GMT+5:30").
export function zoned(date: Date, timeZone: string): Zoned {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    hourCycle: "h23",
    timeZoneName: "short",
  }).formatToParts(date);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  const year = Number(get("year"));
  const month = Number(get("month"));
  const day = Number(get("day"));
  return {
    year,
    month,
    day,
    hour: Number(get("hour")),
    minute: Number(get("minute")),
    weekday: new Date(Date.UTC(year, month - 1, day)).getUTCDay(),
    label: get("timeZoneName"),
  };
}

// "02 Nov 2026, 22:16 GMT+1": the words of the Jinja pages (app/web.py `local_time`), in `timeZone`.
export function formatTime(iso: string, timeZone: string = SERVER_ZONE): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const z = zoned(date, timeZone);
  return `${two(z.day)} ${MONTHS[z.month - 1]} ${z.year}, ${two(z.hour)}:${two(z.minute)} ${z.label}`;
}

// "Mon 2 Nov 2026": the day of a meeting in the Decisions and Review groups, in `timeZone`.
export function formatDay(iso: string, timeZone: string = SERVER_ZONE): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const z = zoned(date, timeZone);
  return `${DAYS[z.weekday]} ${z.day} ${MONTHS[z.month - 1]} ${z.year}`;
}

// The calendar's Upcoming rows (the designer's "Today", "Tomorrow", "Wed 4 Nov" and "14:00") are in
// the viewer's own time zone; `timeZone` is for tests, the browser's own is used when it is absent.
function dateKey(date: Date, timeZone?: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

export function formatEventDay(iso: string, now: Date = new Date(), timeZone?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const key = dateKey(date, timeZone);
  const today = dateKey(now, timeZone);
  if (key === today) return "Today";
  const [y, m, d] = today.split("-").map(Number);
  if (key === new Date(Date.UTC(y, m - 1, d + 1)).toISOString().slice(0, 10)) return "Tomorrow";
  const z = zoned(date, timeZone ?? browserTimeZone());
  return `${DAYS[z.weekday]} ${z.day} ${MONTHS[z.month - 1]}`;
}

export function formatEventTime(iso: string, timeZone?: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("en-GB", { timeZone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
}

// "30 min", "1 hr", "1 hr 30 min": how long an event lasts.
export function formatDuration(startIso: string, endIso: string): string {
  const minutes = Math.round((new Date(endIso).getTime() - new Date(startIso).getTime()) / 60000);
  if (!Number.isFinite(minutes) || minutes <= 0) return "";
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest} min`;
  return rest === 0 ? `${hours} hr` : `${hours} hr ${rest} min`;
}

export function platformLabel(platform: "google_meet" | "zoom" | "teams"): string {
  if (platform === "teams") return "Microsoft Teams";
  return platform === "zoom" ? "Zoom" : "Google Meet";
}

// The length of a meeting as the designer writes it: "46 min", "1 hr", "1 hr 5 min". No recording
// yet reads "In progress" while the meeting is being worked on and "0 min" otherwise.
export function durationLabel(seconds: number | null, inProgress: boolean): string {
  if (seconds === null) return inProgress ? "In progress" : "0 min";
  const minutes = Math.max(0, Math.round(seconds / 60));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `${hours} hr` : `${hours} hr ${rest} min`;
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

// Letters for an avatar: "Ada Example" is "AE", "Ben" is "B".
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  const letters = words.length === 1 ? words[0].slice(0, 1) : words[0].slice(0, 1) + words[words.length - 1].slice(0, 1);
  return letters.toUpperCase();
}
