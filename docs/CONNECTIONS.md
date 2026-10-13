# Jiraya Connections — Phase 2D

Phase 2D replaces developer-owned local OAuth tokens with a product-style connection layer.
The current application still uses one local development user; production sign-in will later
select the same connection records using an authenticated user session.

## Connection architecture

```text
Jiraya user
  -> Connect Gmail
  -> Google OAuth consent
  -> Jiraya callback
  -> encrypted OAuth credentials in PostgreSQL
  -> Gmail tool / matcher / verifier / reconciliator
```

`token.json` remains a legacy development fallback only while Web OAuth is not configured.
Once `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` are set, the database-backed
connection is authoritative.

## One-time Google Cloud setup for the Jiraya application

Use the existing Jiraya Google Cloud project or another project owned by the application.
End users do not create their own Cloud projects.

1. Keep the Gmail API enabled.
2. In Google Auth Platform / Credentials, create an OAuth client of type **Web application**.
3. Add this local authorized redirect URI exactly:

   `http://127.0.0.1:8000/connections/google/callback`

4. Keep the OAuth consent screen in Testing while developing and keep your test Gmail account
   in the allowed Test users list.
5. Copy the Web client ID and client secret into your local `.env`; never commit or share them.

## Local environment

Generate an encryption key locally:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Add the resulting value plus the Google Web OAuth values to `.env`:

```text
TOKEN_ENCRYPTION_KEY=<generated Fernet key>
GOOGLE_OAUTH_CLIENT_ID=<web client id>
GOOGLE_OAUTH_CLIENT_SECRET=<web client secret>
GOOGLE_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/connections/google/callback
FRONTEND_BASE_URL=http://localhost:3000
```

Do not paste these secrets into chat, logs, screenshots, commits, or issues.

## Test sequence

1. Install updated dependencies: `python -m pip install -e ".[dev]"`.
2. Run `python -m pytest`.
3. Start FastAPI on port 8000.
4. Start the Next.js frontend on port 3000.
5. In the Jiraya sidebar, click **Connect** on Gmail.
6. Complete Google's consent screen.
7. Google should redirect through the backend callback and then back to the Jiraya frontend.
8. Gmail should show the connected mailbox address.
9. Run **Test latest Gmail** on an existing automation. It should work through the database-backed connection.
10. Disconnect Gmail. Because Web OAuth is configured, the old local `token.json` will no longer be used as a hidden fallback; Gmail preview should report that Gmail is not connected.

## Next connection step

After this flow is validated, add Gmail `users.watch` + Google Cloud Pub/Sub so mailbox changes can
wake Jiraya without manual polling. The Pub/Sub project/topic belongs to Jiraya, not to each end user.
