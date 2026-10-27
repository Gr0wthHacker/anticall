import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

// The API ships no CORS middleware. For a browser to reach it from another origin, set
// NEXT_PUBLIC_API_BASE_URL=/api-proxy at build time; requests are then rewritten server-side
// to API_PROXY_TARGET (build time, default http://127.0.0.1:8000).
const proxyTarget = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  outputFileTracingRoot: here,
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api-proxy/:path*", destination: `${proxyTarget}/:path*` }];
  },
};

export default nextConfig;
