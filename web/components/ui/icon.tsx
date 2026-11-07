// Line icons from the designer's prototype (Beyric Note Taker.dc.html), drawn in the text colour
// (currentColor) so they follow light and dark mode. Decorative: the control that holds one carries
// the accessible name.
const PATHS = {
  link: ["M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7", "M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"],
  logout: ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "M16 17l5-5-5-5", "M21 12H9"],
  trash: ["M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"],
  copy: ["M9 9h10a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z", "M5 15V5a2 2 0 0 1 2-2h10"],
  check: ["m5 12 5 5 9-10"],
  spinner: ["M12 3a9 9 0 1 0 9 9"],
  rewrite: ["M4 12a8 8 0 0 1 14-5.3L20 9M20 4v5h-5M20 12a8 8 0 0 1-14 5.3L4 15M4 20v-5h5"],
  plus: ["M12 5v14M5 12h14"],
  send: ["M22 2 11 13", "M22 2 15 22l-4-9-9-4 20-7z"],
  menu: ["M4 6h16M4 12h16M4 18h16"],
  close: ["M6 6l12 12M18 6 6 18"],
  chevron: ["m9 6 6 6-6 6"],
  back: ["M19 12H5M11 18l-6-6 6-6"],
  video: ["M5 6h9a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z", "m16 10.5 5-3v9l-5-3"],
  info: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z", "M12 8v5M12 16h.01"],
  warning: ["M12 3 2 20h20L12 3z", "M12 10v4M12 17h.01"],
  mail: ["M5 5h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z", "m3 7 9 6 9-6"],
  lock: ["M7 11h10a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2z", "M8 11V8a4 4 0 0 1 8 0v3"],
  arrow: ["M5 12h14M13 6l6 6-6 6"],
  download: ["M12 3v12M7 10l5 5 5-5M5 21h14"],
  down: ["m6 9 6 6 6-6"],
  plus2: ["M12 5v14M5 12h14"],
  calendar: ["M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z", "M3 10h18M8 3v4M16 3v4"],
  eye: ["M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z", "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"],
  search: ["M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z", "m20 20-3.5-3.5"],
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 16, stroke = 2 }: { name: IconName; size?: number; stroke?: number }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
