// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "@/components/app/shell";

// The phone and tablet navigation (lead-designed, owner's request 2026-11-07) and the sidebar's
// identity block (the designer's name, initials and email, audit row A2).

const route = vi.hoisted(() => ({ path: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => route.path }));

const user = { name: "Adebayo Tosin", email: "adebayo@beyrictech.com" };
const shell = (path?: string) => {
  if (path) route.path = path;
  return (
    <AppShell current="meetings" user={user} tasksNew={2}>
      <p>page body</p>
    </AppShell>
  );
};
const aside = () => document.getElementById("app-sidebar") as HTMLElement;
const menu = () => screen.getByRole("button", { name: "Open menu" });

// Following a link in jsdom would try to navigate; the click still reaches the drawer's handler.
const stopNavigation = (event: Event) => event.preventDefault();

beforeEach(() => {
  route.path = "/";
  document.addEventListener("click", stopNavigation);
});
afterEach(() => {
  document.removeEventListener("click", stopNavigation);
  cleanup();
  vi.unstubAllGlobals();
  document.body.removeAttribute("style");
});

describe("sidebar identity", () => {
  it("shows the person's name, initials and email, never the old placeholder", () => {
    const html = renderToStaticMarkup(shell());
    expect(html).toContain('<div class="sidebar-user-name">Adebayo Tosin</div>');
    expect(html).toContain('<div class="sidebar-user-email">adebayo@beyrictech.com</div>');
    expect(html).toMatch(/class="avatar"[^>]*>AT</);
    expect(html).not.toContain(">Owner<");
  });

  it("falls back to the part of the email before the @ when the account has no name", () => {
    const html = renderToStaticMarkup(
      <AppShell current="meetings" user={{ name: null, email: "ada@example.com" }}>
        x
      </AppShell>,
    );
    expect(html).toContain('<div class="sidebar-user-name">ada</div>');
    expect(html).toContain('<div class="sidebar-user-email">ada@example.com</div>');
    expect(html).toMatch(/class="avatar"[^>]*>A</);
  });

  it("keeps Log out when the session is not known", () => {
    const html = renderToStaticMarkup(<AppShell current="meetings">x</AppShell>);
    expect(html).toContain('action="/logout"');
    expect(html).not.toContain("sidebar-user-name");
  });
});

describe("navigation drawer", () => {
  it("starts closed: the menu button says so and the sidebar is not a dialog", () => {
    render(shell());
    expect(menu().getAttribute("aria-expanded")).toBe("false");
    expect(menu().getAttribute("aria-controls")).toBe("app-sidebar");
    expect(aside().getAttribute("data-open")).toBe("false");
    expect(aside().getAttribute("role")).toBeNull();
  });

  it("keeps the sidebar's items, the user block and Log out inside the drawer", () => {
    render(shell());
    const inside = aside();
    const links = Array.from(inside.querySelectorAll("a")).map((a) => a.textContent ?? "");
    for (const name of ["Meetings", "Tasks", "Projects", "Decisions", "Review", "Team", "Notifications", "Templates", "Vocabulary", "Settings"]) {
      expect(links.some((text) => text.startsWith(name)), name).toBe(true);
    }
    expect(inside.textContent).toContain("Adebayo Tosin");
    expect(inside.textContent).toContain("adebayo@beyrictech.com");
    expect(inside.querySelector('form[action="/logout"] button')).toBeTruthy();
  });

  it("opens as a labelled modal dialog, moves focus in, locks scroll and makes the page inert", () => {
    render(shell());
    fireEvent.click(menu());
    expect(menu().getAttribute("aria-expanded")).toBe("true");
    expect(aside().getAttribute("data-open")).toBe("true");
    expect(aside().getAttribute("role")).toBe("dialog");
    expect(aside().getAttribute("aria-modal")).toBe("true");
    expect(aside().getAttribute("aria-label")).toBe("Main menu");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close menu" }));
    expect(document.body.style.overflow).toBe("hidden");
    expect(document.querySelector(".shell-main")?.hasAttribute("inert")).toBe(true);
    expect(document.querySelector(".skip-link")?.hasAttribute("inert")).toBe(true);
  });

  const expectClosed = () => {
    expect(aside().getAttribute("data-open")).toBe("false");
    expect(aside().getAttribute("role")).toBeNull();
    expect(menu().getAttribute("aria-expanded")).toBe("false");
    expect(document.body.style.overflow).toBe("");
    expect(document.querySelector(".shell-main")?.hasAttribute("inert")).toBe(false);
    expect(document.activeElement).toBe(menu());
  };

  it("closes on Escape and gives focus back to the menu button", () => {
    render(shell());
    fireEvent.click(menu());
    fireEvent.keyDown(document, { key: "Escape" });
    expectClosed();
  });

  it("closes on a click on the scrim", () => {
    const { container } = render(shell());
    fireEvent.click(menu());
    fireEvent.click(container.querySelector(".drawer-scrim")!);
    expectClosed();
  });

  it("closes on the close button", () => {
    render(shell());
    fireEvent.click(menu());
    fireEvent.click(screen.getByRole("button", { name: "Close menu" }));
    expectClosed();
  });

  it("closes when a link in the drawer is clicked (a link to the page you are on too)", () => {
    render(shell());
    fireEvent.click(menu());
    fireEvent.click(Array.from(aside().querySelectorAll("a")).find((a) => a.textContent === "Meetings")!);
    expectClosed();
  });

  it("closes on a route change", () => {
    const { rerender } = render(shell());
    fireEvent.click(menu());
    expect(aside().getAttribute("data-open")).toBe("true");
    rerender(shell("/tasks"));
    expect(aside().getAttribute("data-open")).toBe("false");
    expect(document.body.style.overflow).toBe("");
  });

  it("keeps Tab inside the drawer: last wraps to first, Shift+Tab on the first wraps to the last", () => {
    render(shell());
    fireEvent.click(menu());
    const first = aside().querySelector("a[href]") as HTMLElement; // the logo, ahead of Close menu
    const logout = screen.getByRole("button", { name: "Log out" });
    logout.focus();
    expect(fireEvent.keyDown(logout, { key: "Tab" })).toBe(false);
    expect(document.activeElement).toBe(first);
    expect(fireEvent.keyDown(first, { key: "Tab", shiftKey: true })).toBe(false);
    expect(document.activeElement).toBe(logout);
  });

  it("pulls focus back in if it somehow left the drawer", () => {
    render(shell());
    fireEvent.click(menu());
    menu().focus();
    fireEvent.keyDown(document.body, { key: "Tab" });
    expect(aside().contains(document.activeElement)).toBe(true);
  });

  it("closes when the window grows past the breakpoint", () => {
    let onChange: (() => void) | undefined;
    const query = {
      matches: true,
      addEventListener: (_: string, fn: () => void) => {
        onChange = fn;
      },
      removeEventListener: () => {
        onChange = undefined;
      },
    };
    vi.stubGlobal("matchMedia", () => query);
    render(shell());
    fireEvent.click(menu());
    expect(aside().getAttribute("data-open")).toBe("true");
    query.matches = false;
    act(() => onChange?.());
    expect(aside().getAttribute("data-open")).toBe("false");
  });

  it("leaves scroll alone when it closes, restoring what the page had", () => {
    document.body.style.overflow = "scroll";
    render(shell());
    fireEvent.click(menu());
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(document.body.style.overflow).toBe("scroll");
  });
});
