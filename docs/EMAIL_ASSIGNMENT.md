# Shared email workspace (assessment branch)

The email workspace is an additive experience. Existing Jiraya automations, Calendar,
GitHub, Meet and their deployed settings remain unchanged. The same account owner
can sign in with the same email and password as Client or Assistant, or switch views
after signing in. This view choice only exposes different controls over that owner's
own inbox; it never grants access to another person's mailbox. To work for a
different client, a separately registered delegated assistant still needs a
single-use, 24-hour invitation. The client may revoke that access. Existing Jiraya
accounts migrate to the client account type by default.

## Reviewer walkthrough

1. Register a **Client** account and open Email workspace. Click **Try sample inbox**.
   This creates five clearly marked sample conversations, without contacting Gmail.
2. Switch to **Assistant view** without changing credentials. The assistant view sees
   all five conversations; the client view sees only the two requiring attention.
   You can also sign in with the same credentials in another browser profile and
   select Assistant there.
3. For a real second person, create an invitation in Client view. Register a separate
   delegated assistant account using the sign-up checkbox, and accept the code.
4. Open a routine scheduling conversation. Change its responsibility to **Client to
   reply**. Refresh the client view: it appears there immediately. On the client
   view, choose **Ask my assistant to take over**; it disappears from the client's
   attention list and returns to the assistant's.
5. Open a conversation in the assistant view. **Suggest a draft** invokes Nebius if
   configured; the assistant can instead type one, save it, and use **Simulate reply**
   to complete a sample conversation. No sample email is ever sent.
6. Create a notify-only or review-required Gmail watch under **Gmail watches**.
   **Test rule** takes sample sender/subject/body and evaluates the real matcher and
   action router without sending an email. A missing model key produces a clear
   unavailable error rather than pretending the AI ran.

## Live integration

With a real client Gmail connection, **Sync Gmail** loads up to 15 recent inbox
messages and classifies their threads. New incoming inbox mail is also classified
by the existing background Gmail runtime while a human assistant is linked.
The assistant can review or manually correct assignments and compose a reply.
Sending a live reply requires a deliberate confirmation, the client's connection,
the existing Gmail send scope, and ENABLE_EXTERNAL_ACTIONS. A send with an uncertain
network result stays blocked for review to avoid automatic duplicate sends.

The three responsibility labels are distinct from an automation's action policy:
notify-only does not imply no reply needed. A manual assignment is retained on a
later incoming message; a disagreeing AI suggestion flags the thread for review.
The client sees only open CLIENT_TO_REPLY threads.

## Deployment

Deploy the FastAPI backend and Next.js frontend from the same commit. The backend
performs an additive migration for the user role and creates the new linked-access
and conversation tables on startup. Keep the existing DATABASE_URL, Google OAuth,
TOKEN_ENCRYPTION_KEY and NEBIUS_API_KEY settings. Do not share an assistant's
invitation code publicly. The sample path works without any Google connection;
real Gmail sync and sends still depend on OAuth availability and granted scopes.
Use an isolated staging deployment for assessment rather than changing production
Google OAuth scope requests during an active verification review.

This prototype provides client/assistant views and live Gmail-based workflows, but
it does not connect Outlook. Sample classifications are seeded examples; **Recheck
with AI**, live Gmail ingestion and draft generation invoke the configured Nebius
provider. Review and sender-side checks are needed before using live sending for
high-stakes correspondence.
