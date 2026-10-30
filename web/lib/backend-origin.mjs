// The app owner controls this destination. Requests cannot supply a proxy target.
export function backendOrigin(value) {
  const configured = value?.trim();
  if (!configured) return null;
  const url = new URL(configured);
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  if ((url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) ||
      url.username || url.password || url.search || url.hash || url.pathname !== "/") {
    throw new Error("JIRAYA_BACKEND_URL must be an HTTPS backend origin (or HTTP loopback for development), without a path or credentials.");
  }
  return url.origin;
}
