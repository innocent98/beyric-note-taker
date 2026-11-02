import type { NextConfig } from "next";

// Where unowned paths go. Next stores rewrites in its build output, so this is read when
// `next build` runs (the Dockerfile passes it as a build argument), not when the server starts.
const FASTAPI = process.env.FASTAPI_INTERNAL_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  // FastAPI redirects between "/x" and "/x/" itself; Next must not add its own trailing-slash
  // redirect in front of a path it does not own.
  skipTrailingSlashRedirect: true,
  experimental: {
    // Rewriting notes waits on OpenAI for minutes (nginx allows /meetings/<id>/regenerate 300 s);
    // Next's default proxy timeout is 30 s and would cut those requests off.
    proxyTimeout: 310_000,
  },
  async rewrites() {
    return {
      beforeFiles: [],
      afterFiles: [],
      // Strangler pattern: a page is migrated by adding it under app/. Everything Next does not
      // own (Jinja pages, /api, /health, /static, webhooks) is proxied to FastAPI unchanged.
      fallback: [{ source: "/:path*", destination: `${FASTAPI}/:path*` }],
    };
  },
};

export default nextConfig;
