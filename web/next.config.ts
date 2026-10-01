import type { NextConfig } from "next";

// With NEXT_PUBLIC_API_URL=/api the browser calls this server, which proxies
// to the local API. Other devices (a phone over Tailscale) then need only this
// one origin, and the API stays on localhost.
const apiOrigin = process.env.SCENE_RECALL_API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // We use plain <img> tags for keyframes from the local API,
  // so no remotePatterns needed.
  // The dev server only answers its own hostname; allow Tailscale (MagicDNS) names.
  allowedDevOrigins: ["**.ts.net"],
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiOrigin}/:path*` }];
  },
};

export default nextConfig;
