// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LocalTime } from "@/components/app/local-time";
import { SettingsTimeZone, ZONES, clockIn, offsetLabel } from "@/components/app/settings-time-zone";
import { TimeZoneProvider } from "@/components/app/time-zone-context";
import { ApiError } from "@/lib/api/client";

// The live "Time zone" card: the designer's words, options, "Use my device", the two toasts, and the
// stored zone reaching the pages through the shell's provider.

const mocks = vi.hoisted(() => ({ set: vi.fn(), refresh: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { settings: { timeZone: { set: mocks.set } } } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: mocks.refresh }) }));

const select = () => screen.getByRole("combobox", { name: "Time zone" }) as HTMLSelectElement;

beforeEach(() => {
  mocks.set.mockReset();
  mocks.refresh.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  cleanup();
});

describe("the card", () => {
  it("has the designer's title and line, and offers the designer's 18 zones with their offsets", () => {
    render(<SettingsTimeZone initial="Africa/Lagos" />);
    expect(screen.getByRole("heading", { level: 2, name: "Time zone" })).toBeTruthy();
    expect(screen.getByText("Meeting times, due dates, sprints and reminder emails use this time zone.")).toBeTruthy();
    expect(select().options).toHaveLength(ZONES.length);
    expect(select().value).toBe("Africa/Lagos");
    expect(Array.from(select().options).find((o) => o.value === "Africa/Lagos")?.text).toBe("(UTC+1) Lagos");
    expect(Array.from(select().options).find((o) => o.value === "UTC")?.text).toBe("(UTC+0) Coordinated Universal Time");
    expect(Array.from(select().options).find((o) => o.value === "Asia/Kolkata")?.text).toBe("(UTC+5:30) Mumbai, New Delhi");
  });

  it("adds a zone the list does not have (a device elsewhere) as an extra choice", () => {
    render(<SettingsTimeZone initial="Asia/Kathmandu" />);
    expect(select().value).toBe("Asia/Kathmandu");
    expect(Array.from(select().options).at(-1)?.text).toBe("(UTC+5:45) Kathmandu");
  });

  it("shows the local time in the chosen zone", () => {
    vi.useFakeTimers({ now: new Date("2026-11-05T12:05:00Z"), shouldAdvanceTime: false });
    render(<SettingsTimeZone initial="Africa/Lagos" />);
    expect(screen.getByText("Local time 13:05")).toBeTruthy();
    expect(clockIn("Asia/Tokyo", new Date("2026-11-05T12:05:00Z"))).toBe("21:05");
    expect(offsetLabel("America/New_York", new Date("2026-11-05T12:05:00Z"))).toBe("UTC-5");
  });
});

describe("choosing a zone", () => {
  it("saves it at once, answers with the designer's toast and asks the pages to draw again", async () => {
    mocks.set.mockResolvedValue({ time_zone: "Asia/Tokyo", is_set: true });
    render(<SettingsTimeZone initial="UTC" />);
    await userEvent.selectOptions(select(), "Asia/Tokyo");
    expect(mocks.set).toHaveBeenCalledWith("Asia/Tokyo");
    expect(await screen.findByText("Time zone updated")).toBeTruthy();
    expect(select().value).toBe("Asia/Tokyo");
    expect(mocks.refresh).toHaveBeenCalled();
  });

  it("goes back to the old choice and says why when the API refuses", async () => {
    mocks.set.mockRejectedValue(new ApiError(422, "time_zone_unsupported", "That time zone is not one we know. Pick one from the list."));
    render(<SettingsTimeZone initial="UTC" />);
    await userEvent.selectOptions(select(), "Asia/Tokyo");
    // 422 messages are developer text on the pages, so the owner reads the general line.
    expect((await screen.findByRole("alert")).textContent).toBe("Something went wrong. Please try again.");
    expect(select().value).toBe("UTC");
    expect(screen.queryByText("Time zone updated")).toBeNull();
  });
});

describe("Use my device", () => {
  it("saves the browser's zone and answers with the designer's toast", async () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      timeZone: "Europe/Berlin",
    } as Intl.ResolvedDateTimeFormatOptions);
    mocks.set.mockResolvedValue({ time_zone: "Europe/Berlin", is_set: true });
    render(<SettingsTimeZone initial="UTC" />);
    await userEvent.click(screen.getByRole("button", { name: "Use my device" }));
    expect(mocks.set).toHaveBeenCalledWith("Europe/Berlin");
    expect(await screen.findByText("Time zone set from your device")).toBeTruthy();
    expect(select().value).toBe("Europe/Berlin");
  });
});

describe("the stored zone on the pages", () => {
  it("writes every LocalTime in it, whatever the browser's zone is", () => {
    render(
      <TimeZoneProvider value="Asia/Tokyo">
        <LocalTime iso="2026-11-02T21:16:52Z" />
        <LocalTime iso="2026-11-02T21:16:52Z" kind="day" />
      </TimeZoneProvider>,
    );
    expect(screen.getByText("03 Nov 2026, 06:16 GMT+9")).toBeTruthy();
    expect(screen.getByText("Tue 3 Nov 2026")).toBeTruthy();
  });

  it("falls back to the browser's zone when none is stored", () => {
    render(
      <TimeZoneProvider value={null}>
        <LocalTime iso="2026-11-02T21:16:52Z" />
      </TimeZoneProvider>,
    );
    expect(screen.getByText(/^02 Nov 2026, 21:16 (UTC|GMT)/)).toBeTruthy(); // the suite runs with TZ=UTC
  });
});
