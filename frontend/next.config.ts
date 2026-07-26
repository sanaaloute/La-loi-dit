import type { NextConfig } from "next";

// Server-side proxy target used when NEXT_PUBLIC_API_URL is NOT set. The
// backend does not enable CORS, so by default the browser talks to the
// same-origin path "/backend-api/*" which Next.js rewrites to the API.
// Set NEXT_PUBLIC_API_URL (e.g. http://localhost:8000) to call the API
// directly from the browser — this requires CORS to be handled upstream
// (e.g. via Nginx).
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
};

export default nextConfig;
