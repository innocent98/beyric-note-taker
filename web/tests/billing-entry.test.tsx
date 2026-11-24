// @vitest-environment jsdom
import { join } from "node:path";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "@/components/app/shell";
import { noticeLink, NotificationsView } from "@/components/app/notifications-view";
import { BillingNavLink, SidebarHours } from "@/components/billing/sidebar-billing";
import { HoursReasonLink, isHoursReason } from "@/components/billing/hours-reason-link";
import { forgetBillingEntry } from "@/components/billing/use-billing-state";
import { ApiError } from "@/lib/api/client";
import { ABSENT_FOR_MS, billingEntryHref, billingKnownAbsent, forgetBillingTabState, rememberBillingAbsent } from "@/lib/billing";
import { cap, stateWith } from "./billing-fixtures";
import { read, REPO } from "./helpers";

// The ways into billing from the signed-in app: the Billing link under Configure, the sidebar's hours card, the
// links on the hours notices and on the "Not recorded" reasons. Each shows only when billing is on for the
// person (the billing routes answer 404 otherwise). The states are the captured ones (09 never, 26 active,
// 61 lapsed) and a trial made from 09 with the trial's hours.

const mocks = vi.hoisted(() => ({ state: vi.fn() }));
vi.mock("@/lib/api/browser", () => ({ api: { billing: { state: mocks.state }, notifications: { readAll: vi.fn() } } }));
vi.mock("next/navigation", () => ({ usePathname: () => "/", useRouter: () => ({ refresh: vi.fn() }) }));

const NEVER = () => cap("09-subscription-never.json");
const ACTIVE = () => cap("26-subscription-active.json");
const LAPSED = () => cap("61-subscription-lapsed-after-pass.json");
const TRIAL = () =>
  stateWith("09-subscription-never.json", { state: "trial", hours: { seconds_left: 7200, holdings_seconds: 10800, warning: null } });
const NOT_FOUND = () => new ApiError(404, "not_found", "Not found.");

beforeEach(() => {
  window.sessionStorage.clear();
  forgetBillingEntry();
});
afterEach(() => {
  vi.clearAllMocks();
  cleanup();
});

describe("where an entry point leads", () => {
  it("a live plan goes to Billing; no plan, a trial, a pilot and a lapsed plan go to the plans screen", () => {
    expect(billingEntryHref(ACTIVE() as never)).toBe("/billing");
    expect(billingEntryHref(cap("50-subscription-past-due.json"))).toBe("/billing");
    expect(billingEntryHref(NEVER() as never)).toBe("/billing/plans");
    expect(billingEntryHref(TRIAL() as never)).toBe("/billing/plans");
    expect(billingEntryHref(LAPSED() as never)).toBe("/billing/plans");
  });
});

describe("the sidebar's Billing link and hours card", () => {
  it("lead a person with no plan to the plans screen, and say what they hold", async () => {
    mocks.state.mockResolvedValue(NEVER());
    render(
      <>
        <BillingNavLink current={false} />
        <SidebarHours />
      </>,
    );
    expect((await screen.findByRole("link", { name: "Billing" })).getAttribute("href")).toBe("/billing");
    const card = await screen.findByRole("link", { name: /No plan, No recorded hours/ });
    expect(card.getAttribute("href")).toBe("/billing/plans");
    expect(card.textContent).toContain("Choose a plan");
    expect(mocks.state).toHaveBeenCalledTimes(1);
  });

  it("show a trial's hours and lead to the plans screen", async () => {
    mocks.state.mockResolvedValue(TRIAL());
    render(<SidebarHours />);
    const card = await screen.findByRole("link", { name: /Trial, 2 hours left/ });
    expect(card.getAttribute("href")).toBe("/billing/plans");
    expect(card.textContent).toContain("Choose a plan");
  });

  it("show an active plan's name and hours and lead to Billing", async () => {
    mocks.state.mockResolvedValue(ACTIVE());
    render(<SidebarHours />);
    const card = await screen.findByRole("link", { name: /20 hours a month, 20 hours left/ });
    expect(card.getAttribute("href")).toBe("/billing");
    expect(card.textContent).not.toContain("Choose a plan");
  });

  it("offer a plan to a lapsed person, and Add hours to a person whose plan has no hours left", async () => {
    mocks.state.mockResolvedValue(LAPSED());
    const { unmount } = render(<SidebarHours />);
    expect((await screen.findByRole("link", { name: /left/ })).getAttribute("href")).toBe("/billing/plans");
    unmount();
    forgetBillingEntry();
    mocks.state.mockResolvedValue(stateWith("26-subscription-active.json", { hours: { seconds_left: 0, holdings_seconds: 72000, warning: "out" } }));
    render(<SidebarHours />);
    const card = await screen.findByRole("link", { name: /Out of hours/ });
    expect(card.getAttribute("href")).toBe("/billing");
    expect(card.textContent).toContain("Add hours");
    // Label in name: the accessible name ends with the visible action word, not another one.
    expect(card.getAttribute("aria-label")).toBe("20 hours a month, Out of hours. Add hours");
  });

  it("say 'running low' in words, not in colour alone, and name an active card by what it shows", async () => {
    mocks.state.mockResolvedValue(stateWith("26-subscription-active.json", { hours: { seconds_left: 7200, holdings_seconds: 72000, warning: "low" } }));
    render(<SidebarHours />);
    const card = await screen.findByRole("link", { name: /running low/ });
    expect(card.textContent).toContain("2 hours left, running low");
    expect(card.getAttribute("aria-label")).toBe("20 hours a month, 2 hours left, running low. Open billing");
  });

  it("draw nothing while billing is not available to the person, and remember it for the tab", async () => {
    mocks.state.mockRejectedValue(NOT_FOUND());
    const { container } = render(
      <>
        <BillingNavLink current={false} />
        <SidebarHours />
      </>,
    );
    await waitFor(() => expect(mocks.state).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe("");
    // It is remembered with the time it was learned, so it can expire (see the test below).
    expect(Number(window.sessionStorage.getItem("beyric.billing.unavailable"))).toBeGreaterThan(Date.now() - 5_000);
    cleanup();
    forgetBillingEntry();
    render(<BillingNavLink current={false} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(mocks.state).toHaveBeenCalledTimes(1);
  });

  it("ask again once the remembered 'not available' is a minute old, so a tab opened before billing was switched on gets the link", async () => {
    mocks.state.mockRejectedValue(NOT_FOUND());
    render(<BillingNavLink current={false} />);
    await waitFor(() => expect(mocks.state).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 20));
    cleanup();
    forgetBillingEntry();
    // 30 seconds later: still believed, no new request.
    window.sessionStorage.setItem("beyric.billing.unavailable", String(Date.now() - 30_000));
    render(<BillingNavLink current={false} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(mocks.state).toHaveBeenCalledTimes(1);
    cleanup();
    forgetBillingEntry();
    // 61 seconds later: billing has been switched on meanwhile, and the link appears without a sign-out.
    window.sessionStorage.setItem("beyric.billing.unavailable", String(Date.now() - 61_000));
    mocks.state.mockResolvedValue(ACTIVE());
    render(<BillingNavLink current={false} />);
    expect((await screen.findByRole("link", { name: "Billing" })).getAttribute("href")).toBe("/billing");
    expect(mocks.state).toHaveBeenCalledTimes(2);
  });

  it("treat the old flag '1' that an open tab may hold as expired, and tolerate storage that throws", () => {
    window.sessionStorage.setItem("beyric.billing.unavailable", "1");
    expect(billingKnownAbsent()).toBe(false);
    rememberBillingAbsent(1_000_000);
    expect(billingKnownAbsent(1_000_000 + ABSENT_FOR_MS - 1)).toBe(true);
    expect(billingKnownAbsent(1_000_000 + ABSENT_FOR_MS)).toBe(false);
    // a time in the future (a clock set back) is not believed past the minute
    expect(billingKnownAbsent(1_000_000 - 1)).toBe(false);
    forgetBillingTabState();
    expect(window.sessionStorage.getItem("beyric.billing.unavailable")).toBeNull();
    const blocked = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(billingKnownAbsent()).toBe(false);
    blocked.mockRestore();
    const full = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("full");
    });
    expect(() => rememberBillingAbsent()).not.toThrow();
    full.mockRestore();
  });

  it("draw nothing for a failure that is not a 404, and ask again on the next page", async () => {
    mocks.state.mockRejectedValue(new ApiError(500, "server_error", "Something went wrong."));
    const { container } = render(<SidebarHours />);
    await waitFor(() => expect(mocks.state).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe("");
    expect(window.sessionStorage.getItem("beyric.billing.unavailable")).toBeNull();
  });

  it("forget what they read when someone signs in or out in this tab", async () => {
    mocks.state.mockResolvedValue(ACTIVE());
    const first = render(<BillingNavLink current={false} />);
    await first.findByRole("link", { name: "Billing" });
    first.unmount();
    forgetBillingTabState();
    mocks.state.mockRejectedValue(NOT_FOUND());
    const { container } = render(<BillingNavLink current={false} />);
    await waitFor(() => expect(mocks.state).toHaveBeenCalledTimes(2));
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe("");
  });
});

describe("the owner shell", () => {
  it("lists Billing under Configure beside Settings, current on the billing pages, with the hours card above the name", async () => {
    mocks.state.mockResolvedValue(ACTIVE());
    render(
      <AppShell current="billing" user={{ name: "Ada", email: "ada@example.test" }}>
        <p>page</p>
      </AppShell>,
    );
    const configure = screen.getByRole("navigation", { name: "Configure" });
    const link = await screen.findByRole("link", { name: "Billing" });
    expect(configure.contains(link)).toBe(true);
    expect(link.getAttribute("aria-current")).toBe("page");
    const labels = [...configure.querySelectorAll("a")].map((a) => a.textContent);
    expect(labels).toEqual(["Templates", "Recipes", "Vocabulary", "Settings", "Billing"]);
    const card = await screen.findByRole("link", { name: /20 hours left/ });
    expect(card.nextElementSibling?.className).toContain("sidebar-user");
  });

  it("looks exactly as before for a person who is not on billing", async () => {
    mocks.state.mockRejectedValue(NOT_FOUND());
    render(
      <AppShell current="settings" user={{ name: "Ada", email: "ada@example.test" }}>
        <p>page</p>
      </AppShell>,
    );
    await waitFor(() => expect(mocks.state).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("link", { name: "Billing" })).toBeNull();
    const labels = [...screen.getByRole("navigation", { name: "Configure" }).querySelectorAll("a")].map((a) => a.textContent);
    expect(labels).toEqual(["Templates", "Recipes", "Vocabulary", "Settings"]);
  });
});

describe("the hours notices", () => {
  const notice = (kind: string, meeting_id: number | null = null) => ({
    id: 1,
    kind,
    meeting_id,
    title: "t",
    body: "b",
    severity: "warning" as const,
    created_at: "2026-11-17T10:00:00Z",
    read_at: null,
  });

  it("point at billing only when a billing address is given, and a meeting's own notice still points at the meeting", () => {
    for (const kind of ["usage.hours_low", "usage.hours_out", "pilot.expiry"]) {
      expect(noticeLink(notice(kind))).toBeNull();
      expect(noticeLink(notice(kind), "/billing/plans")).toEqual({ href: "/billing/plans", label: "Choose a plan" });
    }
    expect(noticeLink(notice("usage.hours_out"), "/billing")).toEqual({ href: "/billing", label: "Add hours or open billing" });
    expect(noticeLink(notice("calendar.not_recorded", 7), "/billing")?.href).toBe("/meetings/7");
    expect(noticeLink(notice("calendar.error"), "/billing")).toBeNull();
  });

  it("show the link on the page when billing is on, and not when it is off", async () => {
    const notices = [notice("usage.hours_out")];
    mocks.state.mockResolvedValue(NEVER());
    const on = render(<NotificationsView initial={notices} />);
    expect((await on.findByRole("link", { name: "Choose a plan" })).getAttribute("href")).toBe("/billing/plans");
    on.unmount();
    forgetBillingEntry();
    window.sessionStorage.clear();
    mocks.state.mockRejectedValue(NOT_FOUND());
    const off = render(<NotificationsView initial={notices} />);
    await waitFor(() => expect(mocks.state).toHaveBeenCalledTimes(2));
    await new Promise((r) => setTimeout(r, 20));
    expect(off.queryByRole("link", { name: "Choose a plan" })).toBeNull();
  });
});

describe("the Not recorded reasons", () => {
  it("are recognised by the calendar's own sentences, which are pinned to the server's text", () => {
    const calendarText = read(join(REPO, "app/calendar_text.py"));
    for (const sentence of ["Your recording hours are used up.", "You have no recording hours at the moment."]) {
      expect(calendarText).toContain(sentence);
      expect(isHoursReason(sentence)).toBe(true);
    }
    for (const other of ["Your hours were set aside for another meeting that was running.", "The note taker was busy with other meetings."]) {
      expect(isHoursReason(other)).toBe(false);
    }
  });

  it("get a way to hours only for an hours reason and only when billing is on", async () => {
    mocks.state.mockResolvedValue(ACTIVE());
    const hours = render(<HoursReasonLink reason="Your recording hours are used up." />);
    expect((await hours.findByRole("link", { name: "Add hours" })).getAttribute("href")).toBe("/billing");
    hours.unmount();
    const other = render(<HoursReasonLink reason="The note taker was busy with other meetings." />);
    await new Promise((r) => setTimeout(r, 20));
    expect(other.container.textContent).toBe("");
    other.unmount();
    forgetBillingEntry();
    mocks.state.mockResolvedValue(NEVER());
    const noPlan = render(<HoursReasonLink reason="You have no recording hours at the moment." />);
    expect((await noPlan.findByRole("link", { name: "Choose a plan" })).getAttribute("href")).toBe("/billing/plans");
    noPlan.unmount();
    forgetBillingEntry();
    window.sessionStorage.clear();
    mocks.state.mockRejectedValue(NOT_FOUND());
    const off = render(<HoursReasonLink reason="Your recording hours are used up." />);
    await new Promise((r) => setTimeout(r, 20));
    expect(off.container.textContent).toBe("");
  });
});
