# Connected Delivery Implementation Plan

> **For agentic workers:** Use executing-plans for each isolated lane. Track steps with checkboxes.

**Goal:** Connect customer feedback, tickets, GitHub PRs/releases, AI evidence, and published changelogs.
**Architecture:** Extend Django APIs and React Router web/Space clients. Separate GitHub, release management, intelligence, and feedback modules behind project-scoped contracts.
**Tech Stack:** Django/DRF, PostgreSQL, Celery, React/TypeScript, existing UI components.
**Spec:** docs/superpowers/specs/2026-09-25-product-hub-design.md

## Global constraints
No pushes or production deployment. Existing preview remains intact until integration verification. One writer per worktree. Explicit published-only public data. All new integrations report unconfigured states honestly. Never log credentials. Baseline6c81bf235. Database migrations reserved0144GitHub,0145releases,0146feedback,0147intelligence,merge0148.

## GitHub lane
- [ ] Implement lane-owned plan in feat/github-delivery; configured GitHub App connection with single-use state and verified installation ownership.
- [ ] Signed/deduplicated webhook ingestion and project repository mapping; real ticket-PR links.
- [ ] Project Development UI and workspace connection UI; scoped API+webhook tests.
- [ ] Expose list_issue_pull_requests(issue) and list_project_releases(project) services to release management.

## Feedback lane
- [ ] Implement lane-owned plan in feat/public-feedback; existing Space/Intake authenticated submission flow with bug/feature metadata.
- [ ] Private pending/rejected moderation, reporter-scoped reads and explicit accepted public records.
- [ ] Publish settings and public submit form, permissions regression tests, typecheck.

## Release management lane
Files: models/release.py, app/releases/{api,serializers,urls}.py, space release URLs, web releases page/service, project sidebar link, public changelog page.
- [ ] Add contract tests: foreign issue selection rejected; guest writes forbidden; draft absent publicly; publication exposes only notes/version; disabled board404; cross-projectrelease404; generation failure leaves draft unchanged.
- [ ] Model ProjectRelease(project,name,version,notes,status,app_version,github_release_url,sources) plus ReleaseIssue(project,release,issue). Project/version unique when active. Migration0145.
- [ ] CRUD /api/workspaces/{slug}/projects/{project}/releases/, scoped member read/member write/admin publish; serializers validate issue IDs inproject and bounded plain text.
- [ ] Explicit POST /{release}/publish/ and /unpublish/; public GET /api/public/anchor/{anchor}/releases/ returns {project_name,releases:[{id,name,version,notes,published_at,app_version}]} only published records of enabled board.
- [ ] POST /{release}/generate/ gathers linked ticket/PR evidence and calls generate_release_summary(project,sources,instructions=''); save returned draft notes/source provenance only after successful model call.
- [ ] UI creates/edits draft, selects work items, previews source evidence, generates editable notes, publishes/unpublishes with clear state. Public changelog page renders notes as text, never rawHTML.
- [ ] Run real scratchDB contract tests, typegen/typecheck/build, browser create->link->draft->publish->public visibility.

## Intelligence lane
- [ ] Implement lane plan in feat/release-intelligence, server provider adapter and project-scoped AppConnection/PageReview.
- [ ] Selected project document review and configured-origin bounded safe page capture; provenance, status, timeout/failure, SSRF checks and AI errors tested.
- [ ] Export generate_release_summary(project,sources,instructions='') and PageReviewPanel({workspaceSlug,projectId}) for release UI.

## Widget lane
- [ ] Existing external coding session builds gsd-whats-new.js plus ReleaseEmbedSetup({feedUrl,scriptUrl}) against public feed contract.
- [ ] Accessible launcher/dialog with focus management, no credentialed requests, safe text rendering, loading/error/empty states, mobile layout.
- [ ] Integration verifies script against actual published release feed.

## Integration and final gates
- [ ] Inspect each commit and test evidence. Merge migrations/imports/routes in coordinator tree.
- [ ] Apply migrations only to approved restoredpreview after SQL review; preserve rollbackoldAPI.
- [ ] Build all changed apps; browser verify actual feedback/project/release/public/widget flows and snapshots.
- [ ] User-chosen GitHub installation and app connection verified when details/authorization available; do not mark those complete from mocks.
- [ ] Independent required reviews of exact final candidate, then request per-instance publication approval if outward publication is needed.
