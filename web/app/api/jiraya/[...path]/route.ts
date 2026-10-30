import { backendOrigin } from "@/lib/backend-origin.mjs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const proxyHeaders = {
  "Cache-Control": "private, no-store",
  "X-Jiraya-Proxy": "route-v1",
};

function unavailable(detail: string, status = 503) {
  return Response.json({ detail }, { status, headers: proxyHeaders });
}

async function proxy(request: Request) {
  let origin: string | null;
  try {
    origin = backendOrigin(process.env.JIRAYA_BACKEND_URL);
  } catch {
    return unavailable("The app owner must correct JIRAYA_BACKEND_URL on Vercel and redeploy.");
  }
  if (!origin) {
    return unavailable("The app owner must set JIRAYA_BACKEND_URL on Vercel and redeploy.");
  }
  const incoming = new URL(request.url);
  const backendHost = new URL(origin).host.toLowerCase();
  // Next can represent request.url with its internal hostname. The HTTP Host
  // also identifies an accidental destination pointing back at this frontend.
  if (incoming.origin === origin || request.headers.get("host")?.toLowerCase() === backendHost) {
    return unavailable("JIRAYA_BACKEND_URL must point to the backend, not this frontend.");
  }
  const target = new URL(origin);
  // Assign pathname rather than resolving an untrusted path as an absolute URL.
  target.pathname = incoming.pathname.slice("/api/jiraya".length);
  target.search = incoming.search;

  const headers = new Headers();
  for (const name of ["accept", "accept-language", "content-type", "cookie", "origin", "referer"]) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : await request.arrayBuffer(),
      redirect: "manual", // OAuth redirects must reach the browser with its session intact.
      cache: "no-store",
      signal: AbortSignal.timeout(180_000),
    });
    const responseHeaders = new Headers();
    for (const name of ["content-type", "location", "retry-after", "allow", "content-disposition"]) {
      const value = upstream.headers.get(name);
      if (value !== null) responseHeaders.set(name, value);
    }
    // Never collapse multiple Set-Cookie headers; their Expires values contain commas.
    for (const cookie of upstream.headers.getSetCookie()) {
      responseHeaders.append("set-cookie", cookie);
    }
    for (const [name, value] of Object.entries(proxyHeaders)) responseHeaders.set(name, value);
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    // Do not expose backend response bodies, URLs, OAuth codes, or transport diagnostics.
    return unavailable("Jiraya could not reach its backend. Please try again shortly.", 502);
  }
}

export const GET = proxy;
export const HEAD = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
