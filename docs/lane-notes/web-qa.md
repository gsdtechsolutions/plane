# LANE NOTES — feat/web-qa (Workspace Q&A)

Base: gsd/stage @ 4ca5f12ae (includes ai-ops seed). All work self-contained; zero shared files touched.

## Feature

Workspace Q&A (Rovo Chat / ClickUp Brain parity): `POST /api/workspaces/<slug>/ask/` with
`{"question": str 3..500, "project_id"?: uuid}`. Tokenizes the question (stopwords dropped,
tokens >= 3 chars, max 6), OR-searches non-archived issues on `name`, `description_stripped`,
and `issue_comments__comment_stripped` (distinct, newest first, limit 8). With hits it builds
`[KEY-n]` source blocks (`KEY = {project.identifier}-{issue.sequence_id}`, n = 1..8) and calls
`plane.app.release_intelligence.provider.generate_text(first_hit_project, sources, instructions)`
synchronously (project is required by generate_text — it embeds `project.name`). Returns
`{"answer", "model", "references": [{id, name, display, state, state_group, priority, url}], "message"}`.
Zero hits → `{"answer": null, "references": [], "message": "No matching issues found. Try different words."}`
and the LLM is never called. `IntelligenceError` → `503 {"error": "ai_unconfigured"}`.
Every request writes an `AIActionAudit` row (`action="workspace.ask"`, best-effort).
Web: standalone page at `/<workspaceSlug>/ask` renders the AskPanel — auto-height textarea,
Cmd/Ctrl+Enter submit, answer card with `[KEY-n]` citations rendered as clickable pill chips
(router-navigate to the issue URL), reference rows with state dots, recent-question chips,
`ai_unconfigured` callout and network-error retry. No new dependencies.

## Files added (all new, no shared edits)

Backend:

- `apps/api/plane/app/workspace_qa/__init__.py`
- `apps/api/plane/app/workspace_qa/api.py` — `workspace_member()` (any active role),
  `tokenize()`, `search_issues()`, `WorkspaceAskEndpoint`
- `apps/api/plane/app/workspace_qa/urls.py` — single path `workspaces/<str:slug>/ask/`
- `apps/api/plane/tests/contract/app/test_workspace_qa.py` — 14 contract tests

Web:

- `apps/web/core/services/integrations/workspace-qa.service.ts` — `workspaceQAService.ask()`,
  types, `isAIUnconfigured()`, `askError()`
- `apps/web/core/components/ai-ops/ask-panel.tsx` — AskPanel
- `apps/web/app/(all)/[workspaceSlug]/ask/page.tsx` — standalone route (zero conflicts)
- `LANE-NOTES.md` (this file)

## COORDINATOR WIRING (exact lines) — REQUIRED

Append to **`plane/app/urls/__init__.py`**, at the very bottom (mirrors the existing
`ai_ops` append pattern):

```python

from plane.app.workspace_qa.urls import urlpatterns as workspace_qa_urls
urlpatterns += workspace_qa_urls
```

Nothing else is wired: the view registers itself via the module above; no settings,
celery, or model changes are needed (synchronous endpoint).

## COORDINATOR WIRING (optional, suggestion only)

Sidebar "Ask" trigger: add a link to `/{workspaceSlug}/ask` wherever workspace-level nav
items are declared. One candidate surface is the app rail / workspace sidebar nav list
component — insert an item `{ name: "Ask", href: `/${workspaceSlug}/ask`, icon: MessageCircleQuestion }`
alongside existing workspace links. Purely optional; the route works without any nav entry.

## Validation

- `python -m compileall` on all changed .py files: OK
- `pytest plane/tests/contract/app/test_workspace_qa.py` → **14 passed**, EXIT=0
  (DB: `test_qa_scratch` via `/tmp/qa-db.env`; last run without `--create-db` after fresh create)
- `pnpm -F web exec tsc --noEmit`: see report — baseline errors pre-exist in untouched files;
  zero errors attributable to lane files.

## Fork facts this lane leaned on (for future lanes)

- IssueComment/IssueAssignee extend the fork's `ProjectBaseModel.save()` which reads
  `self.project.workspace` — ORM creates must pass `project=` and `workspace=` explicitly.
- Comment reverse accessor is `issue_comments` (not `issuecomment`); display key field is
  `Issue.sequence_id` (not `sequence`); assignees resolve via `IssueAssignee` rows
  (`issue_assignee` related name), never the stale legacy M2M.
- Provider `generate_text(project, sources, instructions, review=False)` requires a real
  project instance and raises `IntelligenceError` when LLM_API_KEY/LLM_MODEL are unset.
- Contract tests wire un-merged urls with a module-level `urlpatterns = [path("api/", include(...))]`
  plus `settings.ROOT_URLCONF = __name__` (same idiom as `test_release_intelligence.py`).
