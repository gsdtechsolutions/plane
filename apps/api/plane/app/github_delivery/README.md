# GitHub development integration

This integration reads GitHub pull requests and releases into selected projects. It never edits GitHub repositories, issues, pull requests or releases. A merged pull request is evidence for a release; it does not mark work as shipped.

## Click to connect (GitHub App manifest flow)

No instance configuration is required. A workspace administrator — or a project administrator — clicks **Connect GitHub** in the workspace integration settings and chooses:

- **Personal account**: the App is registered on github.com under the connecting user's account.
- **Organization**: they enter the GitHub organization login; the manifest is posted to `<host>/organizations/<org>/settings/apps/new` and the App is owned by that organization. GitHub itself enforces that the connecting user may create GitHub Apps for that organization. An optional organization login is also accepted for GitHub Enterprise, registering under that organization on that server.
- **GitHub Enterprise**: they enter their GitHub Enterprise Server origin (for example `https://github.example.com`); the App is registered on that server. Enterprise-owned apps and enterprise permissions are not part of the manifest flow; without an organization login the App is owned by the connecting user on that server.

The flow, per connection:

1. The board creates a single-use, expiring nonce bound to the signed-in user and workspace (`POST /api/workspaces/<slug>/github-delivery/connect/` with `{account_type, enterprise_url?}`).
2. `GET /api/github-delivery/manifest/start/?state=…` renders an auto-submitting form that POSTs a manifest to `<host>/settings/apps/new`. The manifest pins the redirect, setup and webhook URLs to the board's public origin, requests Metadata/Pull requests/Contents read permissions, and subscribes to pull_request, pull_request_review and release events. "Request user authorization during installation" stays off — authorization is a separate step.
3. GitHub redirects back to `GET /api/github-delivery/manifest/callback/` with a one-time code. The board exchanges it at `POST <api>/app-manifests/<code>/conversions`, receiving the App id, slug, client id/secret, private key (PEM) and webhook secret. These are stored **Fernet-encrypted at rest** in `GitHubApp` (`plane.app.github_delivery.crypto`; derive the key from `GITHUB_DELIVERY_SECRET_KEY` or the Django `SECRET_KEY` — rotating it invalidates stored credentials until each account reconnects).
4. The user is redirected to the installation page, then through `setup/` → GitHub OAuth → `callback/`, which verifies that the authorizing GitHub user can access the installation (and that it belongs to this App) before creating the workspace `GitHubConnection`.

The board's public origin is derived from the request (`X-Forwarded-Host`/`X-Forwarded-Proto` or request host), validated, and pinned into the nonce at connect time. Operators can pin it instead with `GITHUB_APP_BASE_URL` (settings `GITHUB_DELIVERY["BASE_URL"]`); this remains necessary only when the board sits behind a proxy that does not forward host headers. No credential value is ever returned by the API.

Installations and authorization requests require the same signed-in board administrator. The setup flow uses a hashed, expiring, one-use state bound to that user and workspace, rotated between stages. Setup verifies both the App installation and the user's access, and stores only installation/account/repository identifiers. User and installation access tokens are transient. Installations cannot be attached to two board workspaces, and webhook deliveries are attributed strictly to the App whose secret signed them.

An administrator explicitly selects a repository and project after connecting (workspace admins, or the admin of that project). Only repositories in both the App installation and the authorizing user's accessible repository list are offered. They must be an active project member with member or administrator access. Reconnect after adding repository access; new repositories are never mapped automatically. Removing a repository or suspending/deleting the installation stops synchronization. Disconnect retains existing local evidence.

## Background processing and limits

Run Celery beat and a worker subscribed to the dedicated `github-delivery` queue (`celery -A plane worker -Q github-delivery`). `GITHUB_DELIVERY_QUEUE` can override the queue name consistently on API, worker and beat. Existing default-queue workers do not consume these jobs. Webhooks are HMAC-verified against **every stored App's secret** (`WebhookEndpoint.authenticated_app`), reject payloads larger than 1 MiB, persist the GitHub delivery UUID, signing App and body hash, and queue normalization after the database commit. Duplicate delivery processing is inert. Beat requeues committed pending deliveries and pending initial syncs when broker publication was interrupted. Malformed signed payloads are terminal failures without partial projections. Transient projection failures retain their payload for up to five processing attempts, with exponential backoff starting at one minute. Beat recovers due retries even if a worker disappears. Terminal failures clear their payload and remain inert on identical redelivery. A delivery identity must always retain the same body and event type.

Signed PR, review and release events received before authorization are retained for up to 24 hours (at most 200 waiting events per installation and 2000 globally, each subject to the 1 MiB request limit). They are not projected until the installation has a verified workspace connection and that repository is explicitly mapped. Recovery matches App, installation and repository, so an unmapped repository cannot delay a mapped one and one App's events can never bind to another App's installation. Events already bound to a deleted/disconnected connection cannot be rebound to another workspace. Beat clears expired payloads. Installation-created/added events need no projection; pre-connect lifecycle changes are resolved by the callback’s fresh installation and repository checks.

Initial synchronization reads the latest 100 pull requests and releases per mapping; subsequent webhook events keep records current. The project UI returns up to 200 recent records of each kind. Repository selection supports at most 1000 authorized repositories. These bounds are intentional and visible in the UI; this is not a full GitHub historical import.

Pull requests link automatically when their title, body or branch mentions an exact `<PROJECT_IDENTIFIER>-<sequence>` key for an existing work item in that mapped project. Unlinking creates a suppression marker, so future webhook updates do not recreate a link the user removed. Manual URL links accept only `https://<connected host>/<mapped repository>/pull/<number>` and fetch verified metadata. Project guests cannot view private development metadata. Descriptions are stored as plain text, bounded to 20,000 characters, and are not rendered as HTML.

The review label reports the latest received review, not an aggregate approval verdict or GitHub branch-protection eligibility. Merge status and published release evidence remain separate.

## Release adapter

`plane.app.github_delivery.services` exposes:

- `list_issue_pull_requests(issue)` — safe card metadata for visible links.
- `list_project_releases(project)` — project-scoped releases, including bounded private descriptions for authorized internal use.
- `validate_project_pull_requests(project, ids)` — validates up to 100 selected PR UUIDs against project and workspace.
- `get_release_sources(project, pr_ids, release_id=None)` — scoped `{type, id, title, text, url}` evidence for internal release drafting. It raises DRF `ValidationError` for invalid/foreign selections. Disconnected historical evidence remains usable.

Do not expose these private descriptions or repository metadata through a public feed. Public release content must be explicitly selected and published through the release domain's whitelist.

## Registration and verification

Migration `0164_github_click_connect` (follows `0150_merge_delivery_retry`) adds the `GitHubApp` table, per-connection `host`/`app` fields, host-scoped uniqueness, and delivery `app` attribution. Shared registration consists of the GitHub model exports (including `GitHubApp`), GitHub URL list appended to `plane.app.urls`, the task-module import, and the `github_delivery.recover_pending` beat entry. The route owner registers `development/page.tsx` at `/:workspaceSlug/projects/:projectId/development/` and adds the Development navigation item.

Contract tests: `plane/tests/contract/app/test_github_delivery.py`. They use a real scratch database and mock only external HTTP/queue/realtime delivery. Never run test migrations against the restored application database. The settings screen stays honest: every URL GitHub needs is derived from the live request, and no App exists until someone completes the click-to-connect flow.
