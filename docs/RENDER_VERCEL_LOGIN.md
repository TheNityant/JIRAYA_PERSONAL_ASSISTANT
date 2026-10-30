# Vercel frontend + Render backend: first-party sessions

A 200 from POST /auth/login means credentials were accepted, not that the browser retained the session cookie. Check the subsequent GET /auth/me: a null user means no valid session reached it. A 401 from login instead means credentials were rejected. The frontend now reports accepted-but-missing sessions explicitly rather than silently clearing the form.

## Deployment configuration

Use the Next.js Vercel deployment from `phase2e-generic-execution`, Root Directory `web`. This is a server-capable Next deployment, not a static HTML export. Keep backend authentication enabled.

On **Vercel**, set both variables for the deployment environment you are using and rebuild/redeploy:

```
JIRAYA_BACKEND_URL=https://YOUR-BACKEND.onrender.com
NEXT_PUBLIC_JIRAYA_API_URL=/api/jiraya
```

The first is a server configuration value read when Next builds its routing configuration. The second is the client API base. `/api/jiraya/*` is forwarded to the configured backend; auth responses/cookies return through the frontend's origin. Existing direct API configurations and local development remain supported when proxy settings are absent. The proxy destination must be a trusted HTTPS origin, with HTTP loopback permitted for local tests. Never put provider secrets in NEXT_PUBLIC variables.

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

A successful local proxy test establishes routing and cookie round trips in Next, not a verified live Vercel deployment. Inspect production request/response behavior if it differs.

## Regression checks

`node web/tests/api-proxy-config.cjs` checks routing configuration and rejected proxy destinations. With Playwright/Chromium available, `node web/tests/auth-proxy.cjs` exercises AccountGate, cookie forwarding, refresh, an OAuth-style callback, logout, invalid passwords and accepted-but-missing sessions through a real Next server with an isolated backend fixture. Set `AUTH_PROXY_PRODUCTION=1` to build and test `next start` instead of dev mode. The HTTP fixture uses a non-Secure cookie; real production cookies remain Secure/HttpOnly through the unchanged backend session code.

Reference: [Next external rewrites](https://nextjs.org/docs/app/api-reference/config/next-config-js/rewrites).
