# Vercel frontend + Render backend: first-party sessions

A 200 from POST /auth/login means credentials were accepted, not that the browser retained the session cookie. Check the subsequent GET /auth/me: a null user means no valid session reached it. A 401 from login instead means credentials were rejected. The frontend now reports accepted-but-missing sessions explicitly rather than silently clearing the form.

## Deployment configuration

Use the Next.js Vercel deployment from `phase2e-generic-execution`, Root Directory `web`. This is a server-capable Next deployment, not a static HTML export. Keep backend authentication enabled.

On **Vercel**, set both variables for the deployment environment you are using and rebuild/redeploy:

```
JIRAYA_BACKEND_URL=https://YOUR-BACKEND.onrender.com
NEXT_PUBLIC_JIRAYA_API_URL=/api/jiraya
```

The first is a server-only destination read by the explicit Next route handler at `web/app/api/jiraya/[...path]/route.ts`. The second is the client API base, embedded during the frontend build. `/api/jiraya/*` is forwarded to the configured backend; auth responses/cookies return through the frontend's origin. Existing direct API configurations and local development remain supported. The proxy destination must be a trusted HTTPS origin, with HTTP loopback permitted for local tests. Never put provider secrets in NEXT_PUBLIC variables.

The proxy is a deployed server function, not an optional rewrite. Build output must include the dynamic route `ƒ /api/jiraya/[...path]`. `web/vercel.json` pins the Next.js framework, `npm run build` and `.next` output so a dashboard static-output override cannot silently omit the API function. Keep Root Directory **web**. Remove any custom static export. Do not deploy the `.next/static` folder as the whole application.

On **Render**, set:

```
APP_ENV=production
AUTH_REQUIRED=true
FRONTEND_BASE_URL=https://YOUR-FRONTEND.vercel.app
FRONTEND_ORIGINS=https://YOUR-FRONTEND.vercel.app
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=lax
GOOGLE_OAUTH_REDIRECT_URI=https://YOUR-FRONTEND.vercel.app/api/jiraya/connections/google/callback
GOOGLE_CALENDAR_OAUTH_REDIRECT_URI=https://YOUR-FRONTEND.vercel.app/api/jiraya/connections/google/calendar/callback
GITHUB_OAUTH_REDIRECT_URI=https://YOUR-FRONTEND.vercel.app/api/jiraya/connections/github/callback
```

Save and redeploy Render. Register the exact two Google callback URLs in the Google Web OAuth client and the GitHub callback in the GitHub OAuth app. These callbacks must also pass through the frontend proxy: the first-party cookie cannot be sent directly to the Render hostname. Provider authorization still occurs on Google/GitHub's own website. Existing direct backend callbacks can remain for separate development environments.

This does not change the database, encryption key, provider credentials, account passwords, or token storage. Keep their existing values. Do not disable authentication or move session tokens into localStorage.

## Verify

Use the stable configured frontend URL, not an unconfigured Vercel preview URL. Sign in once. In browser Network tools, POST /api/jiraya/auth/login should return 200 and GET /api/jiraya/auth/me should return the signed-in user. Refresh the page and confirm the user persists, then test a provider connection through its updated callback. Sign out and confirm /auth/me returns a null user. Do not share Cookie/Set-Cookie headers or OAuth callback codes in screenshots.

Before signing in, open `/api/jiraya/health`. It should return JSON with `status: "ok"` and the response header `X-Jiraya-Proxy: route-v1`. A JSON **503** naming `JIRAYA_BACKEND_URL` means the route is deployed but its destination is missing/invalid; a JSON **502** means the route cannot reach its backend. An HTML **404** without the proxy marker means this new route did not handle the request: inspect the deployment's source commit, build output and domain assignment. If the marker is present, inspect the configured backend destination and its response instead. Re-entering OAuth credentials cannot fix routing. Environment changes require a new deployment; redeploying an older commit cannot add a route introduced by a newer commit.

A successful local proxy test establishes routing and cookie round trips in Next, not a verified live Vercel deployment. Inspect production request/response behavior if it differs.

## Regression checks

`node web/tests/api-proxy-config.cjs` checks trusted destinations. With Playwright/Chromium available, `node web/tests/auth-proxy.cjs` exercises AccountGate, health, write verbs/query/body forwarding, backend error statuses, multiple cookies, refresh, all three OAuth callback paths, logout, invalid passwords and accepted-but-missing sessions through a real Next server with an isolated backend fixture. Set `AUTH_PROXY_PRODUCTION=1` to build and test `next start` instead of dev mode. After a build, `node web/tests/proxy-status.cjs` verifies JSON diagnostics for missing configuration, self-routing and an unreachable backend. The HTTP fixture uses a non-Secure cookie; real production cookies remain Secure/HttpOnly through the unchanged backend session code.

Reference: [Next backend-for-frontend route handlers](https://nextjs.org/docs/app/guides/backend-for-frontend).
