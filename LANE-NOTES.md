# Lane: sync-health — integrations health grading + AI drift explainer

Branch `feat/sync-health` (base gsd/stage @ 4ca5f12ae). Status: DONE — 16/16
contract tests green (official `--create-db` run, EXIT=0), `compileall` EXIT=0,
web `tsc --noEmit` zero errors in lane files (123 pre-existing baseline errors
in untouched files only).

## What was built

Unique-fork differentiator: one workspace-admin API that grades Asana /
GitHub / Slack sync health per connection (green / amber / red, plus "none"
when a section has no connections) and can produce an LLM plain-language
drift report. Self-contained web settings panel with status dots and an
"Explain with AI" button.

- `apps/api/plane/app/sync_health/api.py` — `SyncHealthEndpoint`
  (GET, workspace_admin, read-only). Grading:
  - Asana: red = cursor > 24h or errors_24h >= 3; amber = cursor > 2h or
    errors_24h >= 1 or warnings_24h >= 3 (skipped/conflict rows); else green.
  - GitHub: red = delivery_errors_24h >= 5 or never delivered; amber on any
    failed delivery in 24h; else green.
  - Slack: red = active + sync_status "error"; amber = active with cursor
    stale > 24h (never synced counts as stale); else green (inactive rows).
  - `?explain=true` → `generate_text(first_mapped_project, [snapshot json
    ≤ 20k], EXPLAIN_INSTRUCTIONS)`; adds `explain` / `explain_model`, or
    `explain: null` + `explain_error: "ai_unconfigured"` (IntelligenceError)
    / `"ai_unavailable"` (any other failure — explain can never 500 the
    endpoint). Every attempt is audited via `log_ai_action(action=
    "integrations.sync_health_report", ...)`. With zero mapped projects the
    explain short-circuits to `ai_unconfigured` without an AI call/audit.
- `apps/api/plane/app/sync_health/urls.py` — single path (below).
- `apps/api/plane/tests/contract/app/test_sync_health.py` — 16 tests.
- `apps/web/core/services/integrations/sync-health.service.ts` — APIService
  subclass + payload types.
- `apps/web/core/components/sync-health/panel.tsx` — status-dot panel with
  expandable detail rows, error badges, loading skeletons, empty state,
  explain callout with model caption.
- No migration (read-only), no celery, no shared-file edits, no new deps.

## EXACT coordinator wiring (the only required step to ship)

### 1. Backend route — `plane/app/urls/__init__.py`

Append at the very bottom, following the existing ai_ops pattern:

```python
from plane.app.sync_health.urls import urlpatterns as sync_health_urls
urlpatterns += sync_health_urls
```

### 2. Web mount — `apps/web/app/(all)/[workspaceSlug]/(settings)/settings/(workspace)/integrations/page.tsx`

Add the import next to the other section imports:

```tsx
import { SyncHealthPanel } from "@/components/sync-health/panel";
```

Then insert this JSX line right after `<PageHead title={pageTitle} />`
(before `{currentWorkspace?.slug && <GithubDeliverySettings ... />}`):

```tsx
{currentWorkspace?.slug && <SyncHealthPanel workspaceSlug={currentWorkspace.slug} />}
```

## Endpoint contract (for reviewers)

`GET /api/workspaces/<slug>/integrations/sync-health/[?explain=true]`
(403 unless WorkspaceMember role 20). Payload:

```jsonc
{
  "asana": [{ "sync_id", "project": {"id","name"}, "connection_name",
              "workspace_gid_masked", "direction", "active", "initial_sync_done",
              "last_synced_at", "cursor_age_minutes", "last_log": {"level","message","created_at"} | null,
              "errors_24h", "warnings_24h", "task_links", "comment_links", "health" }],
  "github": [{ "mapping_id", "repo", "project", "account", "active", "sync_status",
               "sync_error", "last_delivery_at", "delivery_errors_24h",
               "automation_rules", "open_prs_tracked", "last_pr_at", "health" }],
  "slack": [{ "mapping_id", "channel", "project", "team", "active", "sync_status",
              "sync_error", "last_synced_at", "issue_links", "messages_24h", "health" }],
  "overall": { "asana": "green|amber|red|none", "github": "...", "slack": "..." },
  "computed_at": "<ISO>",
  // with ?explain=true:
  "explain": "<3-bullet report>" | null,
  "explain_model": "<model>" | null,
  "explain_error": "ai_unconfigured" | "ai_unavailable" // only when explain is null
}
```

## Validation record

```
cd /workspace/gsd-plane/wt/sync-health/apps/api
# env: /tmp/api-env-new.env + /tmp/health-db.env, SLACK_APP_BASE_URL=
/workspace/gsd-plane/plane/apps/api/.venv/bin/python -m pytest \
  plane/tests/contract/app/test_sync_health.py --create-db -q
  → 16 passed (EXIT=0)

/workspace/gsd-plane/plane/apps/api/.venv/bin/python -m compileall -q \
  plane/app/sync_health/api.py plane/app/sync_health/urls.py \
  plane/app/sync_health/__init__.py plane/tests/contract/app/test_sync_health.py
  → EXIT=0

cd /workspace/gsd-plane/wt/sync-health
corepack pnpm@9.15.0 -F web exec tsc --noEmit
  → EXIT=1 with 123 errors, ALL pre-existing in untouched files;
    0 errors in sync-health.service.ts / sync-health/panel.tsx
```

## Lane implementation notes

- Tests self-wire the URL: `wire_sync_health_urls` (session autouse fixture)
  appends `sync_health/urls.py` patterns onto the already-imported
  `plane.app.urls` module and calls `clear_url_caches()`; idempotent, so it
  stays harmless once the coordinator's include lands.
- `GitHubWebhookDelivery.id` has no default (it stores GitHub's
  X-GitHub-Delivery GUID) — tests pass `id=uuid.uuid4()` explicitly.
- `AsanaSyncLog` (ProjectBaseModel) requires `project=` at creation because
  its save() reads `self.project`.
- GitHub deliveries are correlated to a mapping via
  `(connection_id, repository_id)` (that is how deliveries are stored);
  `open_prs_tracked` filters `remote_closed_at__isnull=True`;
  `automation_rules` counts `GitHubAutomationRule` rows for the mapping's
  project (rules hang off projects, not mappings).
- Django 5 / timezone-safe: `django.utils.timezone.now()` only.
