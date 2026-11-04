import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

// The machine's own zone must not change what a test sees: every test starts in UTC and says so when
// it needs another one (tests/local-time.test.tsx sets process.env.TZ for the zone it is about).
process.env.TZ = "UTC";

export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL(".", import.meta.url)) },
  },
  esbuild: { jsx: "automatic" },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    setupFiles: ["tests/setup.ts"],
  },
});
