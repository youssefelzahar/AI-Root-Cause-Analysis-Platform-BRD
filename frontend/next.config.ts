import type { NextConfig } from "next";

// NOTE: rewrites() is evaluated at BUILD time and serialized into
// .next/routes-manifest.json, so this value is baked into the image and
// changing it requires a rebuild. The Dockerfile passes it as a build ARG.
// API_INTERNAL_URL (used by server components) is read at request time and
// stays runtime-configurable.
const apiProxyTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: {
    proxyClientMaxBodySize: "210mb",
  },
  async rewrites() {
    // The browser always calls same-origin /api, so no NEXT_PUBLIC_* value is
    // inlined into the client bundle and there is no CORS to configure.
    return [{ source: "/api/:path*", destination: `${apiProxyTarget}/api/:path*` }];
  },
};

export default nextConfig;
