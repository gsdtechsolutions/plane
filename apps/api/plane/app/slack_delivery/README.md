# Slack conversations integration

This integration reads Slack channel messages into selected projects. It never joins
channels on its own and never writes to Slack: no posts, no reactions, no invites, no
channel management. A message is evidence for a conversation; nothing is sent back.

## Configure a Slack app

Set these values on the API, Celery worker and Celery beat services:

- `SLACK_CLIENT_ID` and `SLACK_CLIENT_SECRET`: the Slack app credentials (App Credentials → Basic Information).
- `SLACK_SIGNING_SECRET`: the app's Signing Secret, used to verify Events API requests.
- `SLACK_APP_BASE_URL`: the board's public HTTPS origin, without a path. Its `/api/` routes must reach this API. HTTP is accepted only for localhost development.

The workspace integration settings show missing configuration and the exact redirect and
events addresses. No credential value is returned by the API.

Configure the Slack app with:

- Redirect URL: `<SLACK_APP_BASE_URL>/api/slack-delivery/callback/`
- Request URL (Events API): `<SLACK_APP_BASE_URL>/api/slack-delivery/webhooks/`
- Bot token scopes: `channels:read`, `groups:read`, `channels:history`, `groups:history`.
- Event subscriptions: `message.channels`, `message.groups`, `channel_rename`, `app_uninstalled`, `tokens_revoked`.
- Keep the app out of the workflow: installation is a standard user OAuth flow for the
  signed-in board workspace administrator.

Connect, map, and disconnect are workspace administrator actions. The OAuth callback
consumes a hashed, expiring, one-use nonce bound to that board user and workspace, and
workspace membership is rechecked after Slack network calls. A Slack team cannot be
connected to two board workspaces. The bot token is stored encrypted at rest (key derived
from `SECRET_KEY`); only identifiers and the workspace domain are kept in plaintext.
Disconnect clears the stored token and deactivates the connection and its mappings;
existing local evidence is retained. Changing `SECRET_KEY` invalidates stored tokens —
administrators must reconnect.

## Channel mapping

An administrator explicitly maps a channel to a project after connecting. Only channels
the app can see (public, or private when invited) are listed, and only channels the bot
has already been invited to can be mapped — the app never joins by itself, and mapping
is refused for channels it is not a member of. Removing a mapping, revoking the token
(Slack `tokens_revoked`), or uninstalling the app stops synchronization and deactivates
affected mappings. Disconnect retains existing local evidence. `channel_rename` events
keep channel names current; unmapped channels never block mapped ones.

## Background processing and limits

Run Celery beat and a worker subscribed to the dedicated `slack-delivery` queue
(`celery -A plane worker -Q slack-delivery`). `SLACK_DELIVERY_QUEUE` can override the
queue name consistently on API, worker and beat. Existing default-queue workers do not
consume these jobs. Events authenticate with Slack's `v0=` HMAC SHA-256 signature over
`v0:timestamp:body`, reject timestamps skewed more than five minutes, and reject bodies
larger than 1 MiB. The Events API `url_verification` handshake echoes a signed challenge
once. Events persist the Slack `event_id`, so repeated delivery attempts are inert, and
an event identity can never be reused with a different body. Beat requeues committed
pending events and pending initial syncs when broker publication was interrupted.
Malformed signed payloads are terminal failures without partial projections. Transient
projection failures retain their payload for up to five processing attempts, with
exponential backoff starting at one minute. Beat recovers due retries even if a worker
disappears. Signed message events received before the team connects are retained for up
to 24 hours (at most 200 waiting events per team and 2000 globally). They are not
projected until the team has a verified workspace connection and the channel is
explicitly mapped. Beat clears expired payloads.

Initial synchronization reads the latest 100 messages per mapping via
`conversations.history` (joining bot noise such as `channel_join` is skipped);
subsequent Events API messages keep records current, including edits
(`message_changed`) and deletions (`message_deleted`). The project UI returns up to 200
recent messages. Channel selection supports the first 1000 non-archived channels. These
bounds are intentional and visible in the UI; this is not a full Slack archive import.

Messages link automatically when their text mentions an exact
`<PROJECT_IDENTIFIER>-<sequence>` key for an existing work item in that mapped project.
Unlinking creates a suppression marker, so future event updates do not recreate a link
the user removed. Manual permalink links accept only
`https://<team>.slack.com/archives/<mapped channel>/p<ts>` for a connected team, fetch
the exact message via `conversations.replies`, and store verified metadata. Project
guests cannot view project conversation metadata. Message text is stored as plain text,
bounded to 20,000 characters, and is not rendered as HTML.

## Conversation adapter

`plane.app.slack_delivery.services` exposes:

- `list_issue_messages(issue)` — safe card metadata for visible links.
- `list_project_messages(project)` — project-scoped messages.
- `validate_project_messages(project, ids)` — validates up to 100 selected message UUIDs against project and workspace.
- `get_conversation_sources(project, message_ids)` — scoped `{type, id, title, text, url}` evidence for internal release drafting. It raises DRF `ValidationError` for invalid/foreign selections. Disconnected historical evidence remains usable.

Do not expose conversation metadata through a public feed. Public release content must
be explicitly selected and published through the release domain's whitelist.

## Registration and verification

Migration `0160_slack_delivery` depends on `0143_automation_execution`. Shared
registration consists of the Slack model exports, the Slack URL list appended to
`plane.app.urls`, the task-module import, and the `slack_delivery.recover_pending` beat
entry. The route owner registers `conversations/page.tsx` at
`/:workspaceSlug/projects/:projectId/conversations/` and adds the Conversations
navigation item.

Contract tests: `plane/tests/contract/app/test_slack_delivery.py`. They use a real
scratch database and mock only external HTTP/queue/realtime delivery. Never run test
migrations against the restored application database. Verification without live Slack
credentials does not prove a workspace is connected; the settings screen must remain
honest about missing configuration.
