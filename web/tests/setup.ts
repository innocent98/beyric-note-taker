import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Component tests run in jsdom (`// @vitest-environment jsdom` at the top of the file); unmount
// between tests so one test's screen never leaks into the next.
afterEach(() => cleanup());
