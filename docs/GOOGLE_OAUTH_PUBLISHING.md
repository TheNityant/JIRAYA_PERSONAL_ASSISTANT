# Google access: from invited testers to public connections

Jiraya registration, Google authorization, and OAuth app publishing are separate.
An account in Jiraya's database does not add a Google test user. The Google account
selected at consent may also differ from the user's Jiraya login email. Do not
auto-enrol users, grant project IAM/developer roles, or assume mailbox ownership
from registration. OAuth consent establishes access to the selected Google account.

## Supported route for public users

The project owner must configure this in **Google Cloud → Google Auth Platform**.
Changing an environment variable cannot change Google's publishing or review status.

1. Use a production Google Cloud project, separate from local/development testing.
   Enable Gmail API and Google Calendar API. Configure the audience as **External**
   for users outside your Workspace organization.
2. Under **Branding**, provide accurate application identity, a reachable support
   contact, a public home page, privacy policy and applicable terms. Use domains you
   control and can verify. A shared hosting suffix does not establish domain ownership;
   configure a custom domain where required for verification. The privacy policy must
   explain actual Google data collection, storage, deletion and model-provider transfers.
   Do not claim that no third party processes email when it is sent to a model API.
   Public `/privacy` and `/terms` pages are included in the frontend. With the
   current production hostname, enter these links in Branding after deployment:

   - Home: `https://jiraya-personal-assistant.vercel.app/`
   - Privacy: `https://jiraya-personal-assistant.vercel.app/privacy`
   - Terms: `https://jiraya-personal-assistant.vercel.app/terms`

   `/` is a server-rendered public description of the app and its services. It
   requires neither login, JavaScript nor a running backend. Sign-in and the
   automation workspace live at `/app`; do not use `/app` as the OAuth Branding
   home page. The Google site-verification tag remains in the root layout.
   Legacy provider completion redirects to `/?connection=...&status=...` are
   forwarded to `/app` with their result, preserving existing callback settings.
   Keep `FRONTEND_BASE_URL` as the frontend origin, without adding `/app`.

   The operator must review these disclosures against the actual deployed database,
   AI-provider data handling and retention settings before public onboarding.
   Updating pages does not configure a provider's retention or training settings,
   complete a security assessment or establish verified domain ownership. If you
   configure a custom production domain, update all three Branding URLs accordingly.
3. Under **Clients**, create/select a **Web application** OAuth client. Keep the
   client secret on Render only. With the existing Vercel API proxy, register these
   exact redirect URIs (replace the domain if your production domain differs):

   ```text
   https://jiraya-personal-assistant.vercel.app/api/jiraya/connections/google/callback
   https://jiraya-personal-assistant.vercel.app/api/jiraya/connections/google/calendar/callback
   ```

   Configure Render's `GOOGLE_OAUTH_REDIRECT_URI` and
   `GOOGLE_CALENDAR_OAUTH_REDIRECT_URI` to the same respective values. Configure
   `FRONTEND_BASE_URL` and `FRONTEND_ORIGINS` for the actual frontend domain, and
   Vercel's `JIRAYA_BACKEND_URL` for the Render backend origin. The frontend needs
   the dynamic `/api/jiraya/[...path]` proxy; do not deploy it as a static export.
   Keep local callback URLs in the development project rather than this client.
4. Under **Data Access**, declare the scopes the current code actually requests:

   | Connector | Scope | Purpose |
   | --- | --- | --- |
   | Gmail | `https://www.googleapis.com/auth/gmail.readonly` | Read incoming messages for configured automations (restricted) |
   | Gmail | `https://www.googleapis.com/auth/gmail.send` | Send authorized replies (sensitive) |
   | Calendar | `https://www.googleapis.com/auth/calendar.events` | Read/create/update events |
   | Calendar | `https://www.googleapis.com/auth/calendar.events.freebusy` | Check scheduling conflicts |
   | Calendar | `https://www.googleapis.com/auth/calendar.calendarlist.readonly` | Identify the primary calendar and settings |

   The code requests Gmail and Calendar separately; users can connect one without
   connecting the other. Meet attendance uses Calendar links plus a local browser,
   not a new Google Meet OAuth scope. Do not broaden scopes to bypass review.
5. Under **Audience**, select **Publish app** to move from Testing to In production.
   This removes the explicit test-user allowlist requirement. Publishing is not
   verification: sensitive/restricted access may still show warnings, hit an
   unverified-user cap, or be blocked until review. Do not present publishing alone
   as unrestricted production approval.
6. Complete the **Verification Center** process: brand verification, scope
   justifications, demo of consent and each requested permission, and any requested
   additional review. Gmail read access is restricted. Because this application
   handles email through its backend/model provider, plan for Google's restricted
   scope review and required security assessment unless Google confirms an exception.
   Assessment requirements/costs cannot be removed by a Jiraya code change.
7. Optionally set Render `GOOGLE_OAUTH_PUBLISHING_STATUS=production` **after**
   changing the real Google setting. This changes connection help text only. It is
   operator-declared, not a live verification check; it grants no access and never
   gates or bypasses consent. Default `unknown` gives neutral troubleshooting advice.

## While the app remains in testing

Only Google accounts explicitly listed under **Audience → Test users** can authorize
Gmail/Calendar. Google provides no supported public test-user-enrolment API for this
consumer OAuth flow. Do not confuse IAP programmatic clients, IAM roles, or Workspace
domain-wide delegation with this list. Do not automate the Cloud Console as a
registration workaround. Google testing authorizations/refresh tokens normally
expire after seven days for these scopes; saved Jiraya rules do not extend them.

An invite-only beta with manual test-user additions is a temporary alternative while
preparing verification. A request-access queue could collect a user-supplied Google
address with consent, but it still cannot grant Google access automatically. This
change deliberately does not add a queue or send emails: public publishing is the
selected route. No end user needs to create their own OAuth application.

## User experience and errors

Registration and saved automations remain independent of Google permissions. The
Connections panel includes expandable Google help while either connector is
disconnected. Callback errors distinguish cancellation/access denial, organization
restrictions, Workspace administrator policy, and redirect misconfiguration. Google
can display a block on its own page without redirecting back; Jiraya cannot observe
that page or determine which Google account was selected before consent succeeds.
The user can return to Jiraya and open the connection help.

Workspace administrators may still block third-party access after public verification.
GitHub has its own OAuth app configuration/organization restrictions; it does not use
Google's test-user allowlist. No GitHub permission changes are needed for this fix.

## Acceptance test after the Google settings change

1. Use a second Google account **not on the development test-user list**. Register
   a new Jiraya account; confirm registration does not require connecting a provider.
2. Connect Gmail and Calendar independently. Check the consent screen belongs to
   the production client. Any remaining Google review/warning is not a passed release test.
3. Deny consent once; verify clear guidance and that the Jiraya account/rules remain.
4. Grant consent and send a fresh meeting email with date, start and a canonical
   `https://meet.google.com/xxx-yyyy-zzz` link. Verify the event time, default duration
   and original link; retrying the same email must not create duplicate events.
5. Follow [Meet acceptance testing](MEET_INTEGRATION.md) on an awake paired computer.
   The host may need to admit it. Close the website and confirm the helper continues.
6. Sign out/in again: saved connectors and rules must remain. Google revocation or
   expired tokens can still require reconnection; publishing does not make tokens permanent.

## Official references

- [Manage app audience](https://support.google.com/cloud/answer/15549945)
- [Submit the app for verification](https://support.google.com/cloud/answer/13461325)
- [Gmail scope classifications](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [Restricted scope review and security assessment](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)
- [Production projects and domain ownership](https://developers.google.com/identity/verification/authentication-policy-compliance)
- [Google web-server OAuth errors](https://developers.google.com/identity/protocols/oauth2/web-server)
