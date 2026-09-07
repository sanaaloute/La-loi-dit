import type { NextConfig } from "next";

// Server-side proxy target used when NEXT_PUBLIC_API_URL is NOT set: by
// default the browser talks to the same-origin path "/backend-api/*" which
// Next.js rewrites to the API. Set NEXT_PUBLIC_API_URL (e.g.
// http://localhost:8000) to call the API directly from the browser — the
// backend's CORS middleware (LEGAL_AI_CORS_ORIGINS) must then allow this
// origin. Direct streaming is REQUIRED for real-time SSE: this rewrite
// buffers the stream and delivers frames in bursts.
// NOTE: native mobile apps should NOT use this proxy — they call the API
// directly via its own hostname (see docs/deployment-macmini.md, "Public
// API endpoint for mobile apps").
const API_PROXY_TARGET = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/backend-api/:path*",
        destination: `${API_PROXY_TARGET}/:path*`,
      },
    ];
  },
  async headers() {
    // HTML pages must always be revalidated: Next serves statically
    // prerendered pages with `s-maxage=31536000`, which lets browser and
    // carrier/shared caches hold a page whose hashed chunks no longer exist
    // after a redeploy. Hashed assets under /_next keep their immutable
    // year-long cache (correct — their URLs change with every build).
    const noCacheHtml = [{ key: "Cache-Control", value: "no-cache, must-revalidate" }];
    return ["/", "/redaction", "/compte", "/tarifs", "/admin"].map((source) => ({
      source,
      headers: noCacheHtml,
    }));
  },
};

export default nextConfig;
