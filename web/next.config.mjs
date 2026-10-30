// Opt-in same-origin API routing for hosted frontends. The destination is a
// deployment setting, never a browser-supplied URL.
const backend = process.env.JIRAYA_BACKEND_URL?.trim();
if (process.env.NEXT_PUBLIC_JIRAYA_API_URL === "/api/jiraya" && !backend) {
  throw new Error("Set JIRAYA_BACKEND_URL when NEXT_PUBLIC_JIRAYA_API_URL is /api/jiraya.");
}
let destination;
if (backend) {
  const url = new URL(backend);
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if ((url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) ||
      url.username || url.password || url.search || url.hash || url.pathname !== "/") {
    throw new Error("JIRAYA_BACKEND_URL must be an HTTPS backend origin (or HTTP loopback for development), without a path or credentials.");
  }
  destination = url.origin;
}

export default {
  async rewrites() {
    return destination ? [{source: "/api/jiraya/:path*", destination: `${destination}/:path*`}] : [];
  },
  async headers() {
    return [{source: "/api/jiraya/:path*", headers: [{key: "Cache-Control", value: "private, no-store"}]}];
  },
};
