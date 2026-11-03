import { ApiError } from "@/lib/api/client";
import { serverApi } from "@/lib/api/server";

// POST /logout, the same address and method as the Jinja page, so a plain <form method="post"> in
// the sidebar works without JavaScript. It asks FastAPI to end the session (DELETE /api/session,
// which signs out every device), passes FastAPI's new anonymous cookie on to the browser and sends
// the visitor to the login page. The Location is relative so it needs no knowledge of the public host.
export async function POST(request: Request) {
  // This handler reads the CSRF token for the caller (a plain form has none to send), so FastAPI's
  // own check cannot stop a forged logout; this does. A browser always says where a POST came from
  // (Sec-Fetch-Site, or Origin on older ones), and only our own pages are accepted. A request with
  // neither header is not a browser acting for a web page, and the cookie is SameSite=Lax anyway.
  if (!isOwnOrigin(request)) {
    return new Response("Not allowed.", { status: 403, headers: { "Cache-Control": "no-store" } });
  }
  const setCookie: string[] = [];
  const api = await serverApi({
    onResponse: (response) => {
      // Only the last response's cookies count: the DELETE replaces the cookie the GET handed out.
      setCookie.length = 0;
      setCookie.push(...response.headers.getSetCookie());
    },
  });
  try {
    await api.logout();
  } catch (error) {
    // 401: already logged out (expired or revoked elsewhere), which is what was asked for.
    if (!(error instanceof ApiError && error.status === 401)) {
      return new Response("Could not log out just now. Go back and try again.", {
        status: 502,
        headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
      });
    }
  }
  const headers = new Headers({ Location: "/login", "Cache-Control": "no-store" });
  for (const cookie of setCookie) headers.append("Set-Cookie", cookie);
  return new Response(null, { status: 303, headers });
}

export function isOwnOrigin(request: Request): boolean {
  const site = request.headers.get("sec-fetch-site");
  if (site) return site === "same-origin";
  const origin = request.headers.get("origin");
  if (!origin) return true;
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

// A link or a prefetch must never log anyone out.
export function GET() {
  return new Response(null, { status: 303, headers: { Location: "/", "Cache-Control": "no-store" } });
}
