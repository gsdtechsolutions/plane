# GitHub development integration

This integration reads GitHub pull requests and releases into selected projects. It never edits GitHub repositories, issues, pull requests or releases. A merged pull request is evidence for a release; it does not mark work as shipped.

## Configure a GitHub App

Set these values on the API, Celery worker and Celery beat services:

- `GITHUB_APP_ID`: the numeric GitHub App ID.
- `GITHUB_APP_SLUG`: the App's URL slug.
- `GITHUB_APP_CLIENT_ID` and `GITHUB_APP_CLIENT_SECRET`: the App's OAuth credentials.
- `GITHUB_APP_PRIVATE_KEY`: the App's RSA private key in PEM form. Escaped newlines are accepted.
- `GITHUB_APP_WEBHOOK_SECRET`: a strong random webhook secret matching the App configuration.
- `GITHUB_APP_BASE_URL`: the board's public HTTPS origin, without a path. Its `/api/` routes must reach this API. HTTP is accepted only for localhost development.

The workspace integration settings show missing configuration and the exact callback addresses. No credential value is returned by the API.

Configure the App with:

- Setup URL: `<GITHUB_APP_BASE_URL>/api/github-delivery/setup/`
- OAuth callback URL: `<GITHUB_APP_BASE_URL>/api/github-delivery/callback/`
- Webhook URL: `<GITHUB_APP_BASE_URL>/api/github-delivery/webhooks/`
- Repository permissions: Metadata (read), Pull requests (read), Contents (read).
- Webhook events: Pull request, Pull request review, Release. GitHub also sends Installation and Installation repositories events.
- Leave **Request user authorization (OAuth) during installation** off. The setup handler starts a separate OAuth authorization step to prove the signed-in GitHub user can access the installation.

Installations and authorization requests require the same signed-in board workspace administrator. The setup flow uses a hashed, expiring, one-use state bound to that board user and workspace. Setup rotates the state before OAuth; callback consumes it before exchanging the code, verifies both the App installation and the user's access, and stores only installation/account/repository identifiers. User and installation access tokens are transient. Installations cannot be attached to two board workspaces.

An administrator explicitly selects a repository and project after connecting. Only repositories in both the App installation and the authorizing user's accessible repository list are offered. They must be an active project member with member or administrator access. Reconnect after adding repository access; new repositories are never mapped automatically. Removing a repository or suspending/deleting the installation stops synchronization. Disconnect retains existing local evidence.

## Background processing and limits

Run Celery beat and a worker subscribed to the dedicated `github-delivery` queue (`celery -A plane worker -Q github-delivery`). `GITHUB_DELIVERY_QUEUE` can override the queue name consistently on API, worker and beat. Existing default-queue workers do not consume these jobs. Webhooks authenticate the exact request body with HMAC SHA-256, reject payloads larger than 1 MiB, persist the GitHub delivery UUID and body hash, and queue normalization after the database commit. Duplicate delivery processing is inert. Beat requeues committed pending deliveries and pending initial syncs when broker publication was interrupted. Invalid signed payloads are recorded as failed without partial projections. GitHub can redeliver a failed delivery; its identity must retain the same body and event type.

Initial synchronization reads the latest 100 pull requests and releases per mapping; subsequent webhook events keep records current. The project UI returns up to 200 recent records of each kind. Repository selection supports at most 1000 authorized repositories. These bounds are intentional and visible in the UI; this is not a full GitHub historical import.

Pull requests link automatically when their title, body or branch mentions an exact `<PROJECT_IDENTIFIER>-<sequence>` key for an existing work item in that mapped project. Unlinking creates a suppression marker, so future webhook updates do not recreate a link the user removed. Manual URL links accept only `https://github.com/<mapped repository>/pull/<number>` and fetch verified metadata. Project guests cannot view private development metadata. Descriptions are stored as plain text, bounded to 20,000 characters, and are not rendered as HTML.

The review label reports the latest received review, not an aggregate approval verdict or GitHub branch-protection eligibility. Merge status and published release evidence remain separate.

## Release adapter

`plane.app.github_delivery.services` exposes:

- `list_issue_pull_requests(issue)` — safe card metadata for visible links.
- `list_project_releases(project)` — project-scoped releases, including bounded private descriptions for authorized internal use.
- `validate_project_pull_requests(project, ids)` — validates up to 100 selected PR UUIDs against project and workspace.
- `get_release_sources(project, pr_ids, release_id=None)` — scoped `{type, id, title, text, url}` evidence for internal release drafting. It raises DRF `ValidationError` for invalid/foreign selections. Disconnected historical evidence remains usable.

Do not expose these private descriptions or repository metadata through a public feed. Public release content must be explicitly selected and published through the release domain's whitelist.

## Registration and verification

Migration `0144_github_delivery` depends on `0143_automation_execution`. Shared registration consists of the GitHub model exports, GitHub URL list appended to `plane.app.urls`, the task-module import, and the `github_delivery.recover_pending` beat entry. The route owner registers `development/page.tsx` at `/:workspaceSlug/projects/:projectId/development/` and adds the Development navigation item.

Contract tests: `plane/tests/contract/app/test_github_delivery.py`. They use a real scratch database and mock only external HTTP/queue/realtime delivery. Never run test migrations against the restored application database. Verification without live App credentials does not prove a real account is connected; the settings screen must remain honest about missing configuration.
