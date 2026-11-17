import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import PublicLayout from "@/app/(public)/layout";
import { AuthShell } from "@/components/site/auth-shell";

// Markup of a public page as a visitor receives it. The server components that are `async` (the shell
// reads GET /api/session) are awaited one by one; everything else is rendered as React renders it.
// `renderToStaticMarkup` cannot await them itself. `AuthShell` (the account screens' frame) is a plain
// component whose only job is to put `PublicShell` round the page, so it is opened up the same way.
export async function renderPublic(node: ReactElement): Promise<string> {
  let element = node;
  while (typeof element.type === "function" && (element.type.constructor.name === "AsyncFunction" || element.type === AuthShell)) {
    element = await (element.type as (props: unknown) => Promise<ReactElement>)(element.props);
  }
  return renderToStaticMarkup(element);
}

// A page of the `(public)` route group: its content inside app/(public)/layout.tsx, as Next renders it.
export async function renderInPublicLayout(content: ReactNode): Promise<string> {
  return renderPublic(PublicLayout({ children: content }) as ReactElement);
}

export const between = (html: string, from: string, to: string): string => html.slice(html.indexOf(from), html.indexOf(to, html.indexOf(from)) + to.length);
export const header = (html: string): string => between(html, "<header", "</header>");
export const footer = (html: string): string => between(html, "<footer", "</footer>");
export const hrefs = (html: string): string[] => [...html.matchAll(/href="([^"]+)"/g)].map((m) => m[1]);
