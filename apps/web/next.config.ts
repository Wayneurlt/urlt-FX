import type { NextConfig } from "next";

/**
 * The browser always talks to same-origin `/api/v1/*`; Next proxies to the backend.
 * API_ORIGIN is a server-side variable (never NEXT_PUBLIC_) so it is not shipped to the client.
 */
const API_ORIGIN = (process.env.API_ORIGIN ?? "http://localhost:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${API_ORIGIN}/api/v1/:path*`,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
