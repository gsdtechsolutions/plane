# GitHub Delivery Implementation Plan

> **For agentic workers:** Execute this bounded lane inline with the executing-plans workflow; the coordinator owns integration and final review.

**Goal:** Connect an explicitly authorized GitHub App installation to selected project repositories, track PR/release evidence, and expose private Development views.

**Architecture:** A relational sidecar records connections, repository mappings, PRs, releases, issue links, single-use connection nonces, and signed webhook deliveries. GitHub HTTP is server-side and read-only except token exchange; webhook processing is queued, transactional and replay-safe. Existing unrelated integration scaffolding remains separate.

**Tech Stack:** Django/DRF, PostgreSQL, Celery, requests, PyJWT, React, SWR, existing Propel components.

**Spec:** `docs/superpowers/specs/2026-09-25-product-hub-design.md` in the coordinator's product-hub worktree.

## Global Constraints

- Credentials are server-side environment configuration, never browser responses or persisted access tokens.
- No target repository is selected automatically; no outgoing repository writes.
- Workspace administrators connect/map; project members read only their authorized project/ticket records.
- GitHub App permissions: metadata/read, pull_requests/read, contents/read; webhook events pull_request, pull_request_review, release, installation, installation_repositories.
- Setup nonce binds board user/workspace, expires after ten minutes, and rotates before OAuth. Callback verifies both GitHub user installation access and App ownership; repository selection is intersected with the user's accessible repositories.
- Preserve disconnected local history; merged does not imply released.
- Migration 0144 depends on 0143. Do not migrate/restart the restored environment. Test DB is exclusively `test_codex_github_delivery`.

## Task 1: Connection and repository authorization

Files: `models/github_delivery.py`, `app/github_delivery/client.py`, `api.py`, `urls.py`, migration0144, `tests/contract/app/test_github_delivery.py`.

- [x] Add nonce/connection/mapping models and unique installation ownership.
- [x] Test an unauthenticated callback and another board user's nonce rejection before calling GitHub.
  ```python
  response = client.get('/api/github-delivery/callback/', {'state': state, 'code': 'code'})
  assert response.status_code in (401, 403)
  ```
- [x] Implement a bounded HTTPS-only GitHub client using existing requests/PyJWT dependencies. App JWTs authenticate fixed GitHub endpoints; endpoints are fixed API paths, redirects disabled, timeouts explicit.
- [x] Implement start→setup→OAuth callback with nonce consumption under row lock; call `/user/installations`, `/user/installations/{id}/repositories`, and `/app/installations/{id}` before recording access.
- [x] Expose configured/unconfigured state and readable setup URLs; reject nonexistent/private project mapping outside membership scope.
- [x] Verify forged installation ID, callback replay, revoked membership, and unselected repository mapping all fail without creating a connection/mapping.

## Task 2: PR/release synchronization and ticket linking

Files: `app/github_delivery/services.py`, `tasks.py`, `api.py`, sidecar models, same contract test module.

- [x] Verify HMAC SHA256 on exact request bytes before JSON parsing; constrain delivery UUID, event, and body size.
  ```python
  signature = 'sha256=' + hmac.new(secret, raw, hashlib.sha256).hexdigest()
  assert webhook(raw, signature).status_code == 202
  assert webhook(raw, 'sha256=' + '0' * 64).status_code == 403
  ```
- [x] Store unique delivery and enqueue after commit. Worker locks the delivery and active connection/mapping before applying data; repeated processing is inert.
- [x] Recognize only the mapped project's ticket keys in title/body/head branch. Preserve manual links and explicit unlink suppression.
- [x] Support explicit PR URL linking by fetching that PR from an active mapped repo; prevent external-host URLs and cross-project PR IDs.
- [x] Expose `list_issue_pull_requests(issue)` and `list_project_releases(project)` as private metadata adapters. PR state is open/closed/merged; release membership is never inferred.
- [x] Test signature replay, cross-project references, stale updates, disconnect, repeated task processing, manual unlink suppression, and release metadata.

## Task 3: Setup and Development surfaces

Files: `services/github-delivery.service.ts`, `components/github-delivery/{settings,development,issue-development}.tsx`, workspace integration page, issue widgets, project development route page.

- [x] Render an honest unconfigured state with required permissions/callback URLs; Connect is disabled until server configuration exists.
- [x] Render installation/repository selection and explicit project mapping; disable pending actions and report failed HTTP responses.
- [x] Show private project PR/release evidence and ticket Development links, including manual link/unlink controls.
- [ ] Coordinator registers the provided project Development route and navigation item; this lane deliberately leaves shared navigation files untouched.
- [x] Run existing formatting/types tools without reinstalling dependencies.
- [ ] Coordinator browser gate: verify empty/unconfigured and test-record states in the combined preview.

## Verification and handoff

- [x] Run focused real PostgreSQL tests with only GitHub HTTP and external queue boundaries mocked. Keep Redis/Celery test delivery away from live services.
- [x] Generate/check migration0144, lint changed Python/TS, document exact server configuration and private adapter schemas.
- [x] Commit locally with exact tests and browser limitations; no push, deployment, or simulated connection claim.

Verification evidence: seven contract tests passed; final cascade/queue and expiry/malformed regressions passed (two targeted). Migration0144 created seven tables and 30 constraints in a temporary scratch schema and was rolled back. Ruff/OxLint/OxFmt passed; route type generation and final TypeScript check run before local handoff. Real GitHub installation remains unverified without credentials and an explicitly selected repository.
