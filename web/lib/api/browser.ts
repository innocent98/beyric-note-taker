import { createApi } from "./client";

// The API as the browser calls it: same origin (nginx and Next pass /api/* to FastAPI), so the
// session cookie travels by itself and a login's Set-Cookie lands where it should. Writes from
// here are what keep working while a slow rewrite runs, see docs/sop/2026-11-03-nextjs-owner-screens.md.
// A 401 anywhere sends the owner to the login page, once, for every screen.
export const api = createApi({
  onUnauthorized: () => {
    if (typeof window !== "undefined" && window.location.pathname !== "/login") {
      // A full load, so the server renders the login page fresh.
      window.location.href = "/login";
    }
  },
});
