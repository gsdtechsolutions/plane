---
name: verify-plane
description: Verify the GSD Plane fork (plane.gsdut.dev) end to end. Builds and runs this checkout's real Coolify deploy compose as an isolated local stack, seeds it, and drives the web app and API like a user, with evidence. Use before merging or deploying any change to apps/api, apps/web, or deployments/coolify, and for post-deploy smoke checks against production.
---

# Verify Plane

The target is the stack Coolify deploys: `deployments/coolify/compose.yml` built from this checkout. `bin/stack.sh` runs that exact file under its own compose project with a small override, so env mapping, entrypoints, migrations, Caddy proxy and Django settings match production. The override changes only five things:

- **Image names.** Images are named per project, so two checkouts never overwrite each other's builds.
- **Traefik labels.** The proxy loses them. This host runs Coolify too, and those labels would route `plane.gsdut.dev` to the local stack.
- **Network.** The proxy leaves the external `coolify` network.
- **Port.** The proxy publishes on `127.0.0.1:$PLANE_VERIFY_PORT`, default `38080`.
- **Static rate limit.** The web container's Caddy limit goes from 300 to 5000 requests per client IP per minute. In production, Cloudflare caches `/assets` in front of the origin. Here every request comes from one IP with no cache, and one cold workspace load makes about 400 requests. With 300, the local stack 429s its own page loads.

`.env` lives in `~/.local/state/plane-verify/<project>/`. It holds random secrets plus `SESSION_DURATION=2592000`, the value Coolify stores for production. Never copy production secrets into it.

Helpers, all run from the checkout under test:

```bash
S=.claude/skills/verify-plane/bin
$S/stack.sh build    # build images from this checkout; records the commit
$S/stack.sh up       # start, wait until / and /api/instances/ answer 200, print READY
$S/stack.sh doctor   # read-only health check, prints DOCTOR OK or DOCTOR FAIL
$S/stack.sh seed     # instance admin, user, workspace (idempotent)
$S/stack.sh psql "select count(*) from sessions"   # read DB state
$S/stack.sh logs api # last 200 lines of a service
$S/stack.sh env      # BASE, PROJECT, STATE, EVIDENCE
$S/stack.sh down     # remove this project's containers and volumes; evidence stays
$S/session-journey.sh   # auth session journey over HTTP, see features/auth-session.md
node $S/browser-session.mjs   # same journey in headless Chromium: UI sign-in, reloads, screenshots
```

Set `PLANE_CHECKOUT` to verify another worktree, and `PLANE_VERIFY_PROJECT` plus `PLANE_VERIFY_PORT` to run two stacks side by side. Never drive a stack this run did not start. Never point the stack's port or labels at the Coolify network.

## Launch

1. `$S/stack.sh build`. The first build takes 10 to 20 minutes, mostly the four Node apps. Later builds reuse the Docker layer cache.
2. `$S/stack.sh up`. Compose starts `plane-db`, `plane-redis` and `plane-minio`. The migrator runs every migration on the empty database, then the API registers the instance and creates the bucket. `up` returns `READY http://localhost:38080` once `/` and `/api/instances/` both answer 200.
3. `$S/stack.sh seed`. It signs up `verify-admin@example.com` through the real god-mode endpoint, which marks the instance set up. Then it creates `verify-user@example.com` (password `Verify-Plane-2026!`), onboarded, as an admin of workspace `verify`. It prints `seeded verify-user@example.com workspace verify`.

The minio image is `minio/minio:latest` with `pull_policy: never`. Docker Hub and quay.io no longer serve it anonymously. If `docker images minio/minio` is empty, copy the production host's image once:

```bash
ssh root@31.97.98.123 "docker save minio/minio:latest | gzip -1" | gunzip | docker load
```

## Doctor

Run `$S/stack.sh doctor` first, and again whenever anything looks off. It checks:

- The checkout commit matches the commit the images were built from. If not, it prints `WARN: images are older than the checkout`.
- Every service is running and the migrator exited 0.
- The port belongs to this project's proxy container.
- `/` and `/api/instances/` return 200.
- The API process's effective session settings, printed as `SESSION_COOKIE_AGE <n> SESSION_SAVE_EVERY_REQUEST <bool>`.

Only `DOCTOR OK` means the stack is worth driving.

## Drive

Read `features/README.md`, then the feature file for what you changed. Two harnesses:

- **HTTP.** Use curl with a cookie jar against `$BASE`, the same endpoints the web forms post to. `bin/session-journey.sh` is the worked example. Form posts need `csrfmiddlewaretoken` from `GET /auth/get-csrf-token/`. JSON writes need the `X-CSRFToken` header.
- **Browser, scripted.** `bin/browser-session.mjs` uses `playwright-core` from `/workspace/gsd-plane/e2e/node_modules` and the Chromium in `~/.cache/ms-playwright/chromium-1243`, with `LD_LIBRARY_PATH=/workspace/gsd-plane/chromelibs/usr/lib/x86_64-linux-gnu`. Override these with `PLAYWRIGHT_CORE`, `CHROME` and `CHROME_LIBS`. It is the worked example of the UI selectors.
- **Browser, interactive.** Use T3 `preview_*` tools against `http://localhost:38080`. On the sign-in page, fill `#email` and click `button[type=submit]`. Then fill `#password` and click `form:has(#password) button[type=submit]`. Wait for the URL to contain `/verify/`. A reload is `preview_evaluate` with `location.reload()`, followed by `preview_wait_for`. If T3 rejects the thread credential, use the scripted harness.

## Evidence

Evidence goes to `~/.local/state/plane-verify/evidence/<UTC timestamp>-<journey>/`. `stack.sh down` never touches that directory. A proof needs:

- **The real user path.** Form posts and the browser, not Django shell setters. Seeding is the only shell use.
- **The action and the resulting state.** For example, the response headers of the request plus the DB row it changed.
- **Side effects.** Check them in the database with `stack.sh psql` as well as in what the user sees.
- **Screenshots.** Save browser proof with `preview_snapshot` and `save: true`, then copy the PNG into the evidence directory.
- **No live session keys.** Strip session cookie values from saved files. `session-journey.sh` deletes its cookie jar.

A verdict is PASS, FAIL or INCONCLUSIVE. A stack that failed doctor gives INCONCLUSIVE, never PASS.

## Cleanup

`$S/stack.sh down` removes only this project's containers, networks and volumes. Images stay for the next build cache. Remove them with `docker rmi $(docker images -q 'plane-verify-*')` only when you want the disk back. Close every preview tab you opened. Never kill containers by name pattern, and never touch `*-dggjbvxupxjhvczgqdlqwmez-*` containers. Those are production, on the US VPS.

## Production smoke

After a deploy, run the HTTP journey against production with the test account:

```bash
REMOTE_BASE=https://plane.gsdut.dev EMAIL=rt-a@gsdtest.dev PASSWORD='GsdRealtime1!' MIN_AGE=2592000 $S/session-journey.sh
```

It skips the database checks, signs out at the end, and writes evidence the same way.
