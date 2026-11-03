// Beyric Notes service worker. It exists for one reason: Chrome only offers its own install prompt
// to a site with a working fetch handler (developer.chrome.com/blog/update-install-criteria), and
// installing is what puts the app in Android's share sheet (app/manifest.ts `share_target`).
// It is network-only on purpose. It caches nothing and stores nothing: pages and /api responses are
// private to the signed-in person and must never be kept. A page navigation that cannot reach the
// network gets a short offline notice instead of the browser's error page. Everything else (API
// calls, scripts, images, and any POST such as the share itself) is left to the browser untouched.
self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.mode !== "navigate" || request.method !== "GET") return;
  event.respondWith(
    fetch(request).catch(
      () =>
        new Response(
          "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>" +
            "<title>Offline</title><h1>You are offline</h1><p>Beyric Notes needs a connection. Try again when you are back online.</p>",
          { status: 503, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } },
        ),
    ),
  );
});
