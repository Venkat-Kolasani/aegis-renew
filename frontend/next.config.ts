import type { NextConfig } from "next";

// Server-only origin for rewrites; do not expose backend host via NEXT_PUBLIC_*.
const backendOrigin = (process.env.AEGIS_API_ORIGIN || "http://localhost:8000").replace(
  /\/$/,
  "",
);

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/aegis-api/:path*",
        destination: `${backendOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
