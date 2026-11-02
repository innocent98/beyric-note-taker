// The one place outside tokens.css that holds colour literals. Manifest and theme-color metadata
// cannot read CSS variables, so the values are repeated here and tests/guards.test.ts fails if
// they drift from tokens.css.
export const BRAND = {
  teal: "#0E6B5E",
  bgLight: "#F6F7F6",
  bgDark: "#111513",
} as const;
