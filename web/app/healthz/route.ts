// Liveness of Next itself, for the container healthcheck and deploy/deploy.sh. `/health` is
// FastAPI's (it falls through the proxy), so it also proves the whole chain; this one does not.
export const dynamic = "force-dynamic";

export function GET() {
  return new Response("ok", { headers: { "Content-Type": "text/plain", "Cache-Control": "no-store" } });
}
