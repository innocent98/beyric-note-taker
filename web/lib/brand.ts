// The one place outside tokens.css that holds colour literals. Manifest and theme-color metadata
// cannot read CSS variables, so the values are repeated here and tests/guards.test.ts fails if
// they drift from tokens.css. The designs are light only, so there is one theme colour.
// The six project swatches the designer drew (`PCOLORS`); the API stores the hex. styles/tokens.css
// holds the same six as --swatch-1 to --swatch-6 and tests/guards.test.ts checks they agree.
export const PROJECT_COLOURS = ["#0E6B5E", "#3C4A7A", "#8A4B00", "#8A2E5C", "#1F6FB2", "#4A524E"] as const;

export const BRAND = {
  teal: "#0E6B5E",
  bgLight: "#F6F7F6",
} as const;
