"use client";

import { useRef, useState, useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";
import { Alert, Toast } from "@/components/ui";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/browser";
import { errorMessage } from "@/lib/api/client";
import { browserTimeZone } from "@/lib/format";

// The "Time zone" card of the designer's Settings (`TZ`, `tzOpts`, `onTz` and `detectTz` in
// Beyric Note Taker.dc.html): the zone meeting times, due dates, sprints and reminder emails are
// written in. Choosing one (or "Use my device") is saved at once and answered with the designer's
// toast. The zone also reaches the pages: after a save the server render is asked again, so every
// time on the page changes to it.
export const ZONES: ReadonlyArray<readonly [string, string]> = [
  ["Pacific/Honolulu", "Honolulu"],
  ["America/Los_Angeles", "Los Angeles"],
  ["America/Denver", "Denver"],
  ["America/Chicago", "Chicago"],
  ["America/New_York", "New York"],
  ["America/Sao_Paulo", "São Paulo"],
  ["UTC", "Coordinated Universal Time"],
  ["Europe/London", "London"],
  ["Africa/Lagos", "Lagos"],
  ["Europe/Berlin", "Berlin"],
  ["Africa/Johannesburg", "Johannesburg"],
  ["Africa/Cairo", "Cairo"],
  ["Africa/Nairobi", "Nairobi"],
  ["Asia/Dubai", "Dubai"],
  ["Asia/Kolkata", "Mumbai, New Delhi"],
  ["Asia/Singapore", "Singapore"],
  ["Asia/Tokyo", "Tokyo"],
  ["Australia/Sydney", "Sydney"],
];

// "UTC+1", "UTC+5:30", "UTC+0": the designer's `off`.
export function offsetLabel(zone: string, at: Date = new Date()): string {
  try {
    const part = new Intl.DateTimeFormat("en-US", { timeZone: zone, timeZoneName: "shortOffset" })
      .formatToParts(at)
      .find((p) => p.type === "timeZoneName");
    return part ? part.value.replace("GMT", "UTC").replace(/^UTC$/, "UTC+0") : "";
  } catch {
    return "";
  }
}

// "14:05": the designer's `now`.
export function clockIn(zone: string, at: Date = new Date()): string {
  try {
    return new Intl.DateTimeFormat("en-GB", { timeZone: zone, hour: "2-digit", minute: "2-digit" }).format(at);
  } catch {
    return "";
  }
}

// A zone outside the designer's list (a device in Kolkata's neighbour, say) is added as its last
// part: "Asia/Kathmandu" is "Kathmandu".
function zoneOptions(current: string, at: Date): Array<{ value: string; label: string }> {
  const list = ZONES.some(([value]) => value === current)
    ? ZONES
    : [...ZONES, [current, current.split("/").pop()!.replace(/_/g, " ")] as const];
  return list.map(([value, name]) => ({ value, label: `(${offsetLabel(value, at)}) ${name}` }));
}

const subscribe = () => () => {};

function Clock() {
  return (
    <svg
      className="pl-globe"
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}

export function SettingsTimeZone({ initial }: { initial: string }) {
  const router = useRouter();
  const [value, setValue] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [toast, flash] = useToast();
  const latest = useRef(0);
  // The offsets and the clock depend on the moment and on the browser; they are drawn once the page
  // is in the browser so the server render and the first browser render agree.
  const inBrowser = useSyncExternalStore(subscribe, () => true, () => false);
  const at = new Date();

  async function save(next: string, message: string) {
    const before = value;
    const mine = ++latest.current;
    setValue(next);
    setError(null);
    try {
      const saved = await api.settings.timeZone.set(next);
      if (latest.current !== mine) return;
      setValue(saved.time_zone);
      flash(message);
      router.refresh();
    } catch (caught) {
      if (latest.current !== mine) return;
      setValue(before);
      setError(errorMessage(caught));
    }
  }

  const options = inBrowser
    ? zoneOptions(value, at)
    : (ZONES.some(([zone]) => zone === value) ? ZONES : [...ZONES, [value, value] as const]).map(([zone, name]) => ({
        value: zone,
        label: name,
      }));

  return (
    <section className="card pl-language" aria-labelledby="set-time-zone">
      <div className="pl-language-text">
        <h2 id="set-time-zone" className="set-title">
          Time zone
        </h2>
        <p className="set-sub">Meeting times, due dates, sprints and reminder emails use this time zone.</p>
      </div>
      <div className="pl-language-control">
        <div className="pl-language-select">
          <Clock />
          <select
            className="select pl-lang"
            aria-label="Time zone"
            value={value}
            onChange={(event) => void save(event.target.value, "Time zone updated")}
          >
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <div className="tz-foot">
          <span>{inBrowser ? `Local time ${clockIn(value, at)}` : ""}</span>
          <button
            className="tz-device"
            type="button"
            onClick={() => void save(browserTimeZone(), "Time zone set from your device")}
          >
            Use my device
          </button>
        </div>
        {error ? <Alert kind="error">{error}</Alert> : null}
      </div>
      <Toast message={toast} />
    </section>
  );
}
